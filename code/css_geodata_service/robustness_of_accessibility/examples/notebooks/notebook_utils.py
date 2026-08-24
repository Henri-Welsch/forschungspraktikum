from __future__ import annotations
from enum import StrEnum
import os
from pathlib import Path
import geopandas as gpd
import osmnx as ox
import pandas as pd


class HazardEventLikelyhood(StrEnum):
    HQ100 = "M"
    HQ200 = "L"
    HQ50 = "H"


class RoaNotebookConfig:
    place_name: str = "Trier, Germany"
    network_type: str = "drive_service"
    undirected_graph: bool = True
    event = HazardEventLikelyhood.HQ100
    assume_bridges_as_unaffected: bool = True


def set_notbook_wd():
    """Sets the process working directory to the 'code' root directory."""
    code_dir = Path(__file__).resolve().parents[4]
    if Path().resolve() != code_dir:
        os.chdir(code_dir)
    print(f"Working dir set to: {os.getcwd()}")


def get_working_directory() -> Path:
    """Returns the absolute path to the 'code' root directory."""
    return Path(__file__).resolve().parents[4]


def get_roa_base_path() -> Path:
    """Returns the absolute path to 'robustness_of_accessibility' directory."""
    return Path(__file__).resolve().parents[2]


def get_roa_data_path() -> Path:
    return get_roa_base_path() / "data"


def get_roa_inputs_path() -> Path:
    return get_roa_data_path() / "input"


def get_roa_outputs_path() -> Path:
    return get_roa_data_path() / "output"


def get_roa_cache_path() -> Path:
    return get_roa_data_path() / "processed"


def get_roa_flooding_path() -> Path:
    return get_roa_inputs_path() / "Flooding"


def get_roa_hazard_areas_path() -> Path:
    return get_roa_flooding_path() / "HazardAreas"


def get_roa_hq_raw_path() -> Path:
    candidate = get_roa_hazard_areas_path() / "HQ_raw"
    if candidate.is_dir():
        return candidate
    candidate_flooding = get_roa_flooding_path() / "HQ_raw"
    if candidate_flooding.is_dir():
        return candidate_flooding
    return candidate


def get_roa_hazard_data_path(
    event: HazardEventLikelyhood,
    region_modifier: str | None = "_cropped_trier",
    file_type: str | None = None,
) -> Path:
    """
    This helper function allows for easy access to different files based on event: HazardEventLikelyhood

    There are major assumptions concearning file path resolution:
    - Is not specified otherwise the function will return the path to the data for region "Trier"
    - Data for germany (region_modifier == None) is usually provided as .gml
    - Data cropped from to fit a region (region_modifier != None= is usually provided as .geojson
    """
    if file_type is None:
        if region_modifier is None:
            file_type = ".gml"
        else:
            file_type = ".geojson"
    return get_roa_hazard_areas_path() / f"nz_hazardArea_fluival_{event.value}-DE{region_modifier}{file_type}"


def load_or_fetch_osm_features(
    cache_path: Path,
    polygon,
    tags: dict,
) -> gpd.GeoDataFrame:
    """Load a GeoDataFrame of OSM features from a cached GeoJSON file, or
    fetch it from OpenStreetMap via osmnx and persist it to *cache_path* for
    reuse by subsequent notebook runs.

    Shared by notebooks that need the same POI/infrastructure feature types
    (hospitals, fire stations, power/water infrastructure, ...) so the
    fetch-and-cache pattern isn't duplicated per notebook.
    """
    if cache_path.exists():
        return gpd.read_file(cache_path)
    gdf = ox.features_from_polygon(polygon=polygon, tags=tags)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(cache_path, driver="GeoJSON")
    return gdf


def unwrap_nested_list(lst):
    """Unwrap a list of lists into a single list. Used when identifying nodes in a graph by edges"""
    result = []
    for item in lst:
        if isinstance(item, list) or isinstance(item, set):
            result.extend(unwrap_nested_list(item))
        else:
            result.append(item)
    return result


def add_district_names_to_scores(admin_boundaries: gpd.GeoDataFrame, scores: pd.DataFrame):
    districts_details: list[dict] = list()
    for idx_district, row_district in admin_boundaries.iterrows():
        district_name = row_district["name"]
        sampled_nodes_within_this_district = scores[scores.intersects(row_district["geometry"])]
        for idx_node, row_node in sampled_nodes_within_this_district.iterrows():
            districts_details.append({"node_id": idx_node, "district_name": district_name})
    return pd.DataFrame(districts_details)
