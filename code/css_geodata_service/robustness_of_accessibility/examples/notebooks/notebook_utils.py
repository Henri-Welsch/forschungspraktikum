from __future__ import annotations
from enum import StrEnum
import os
from pathlib import Path
import geopandas as gpd
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
    notebook_path = Path().resolve()

    # Walk up to the desired parent (e.g., 'css_geodata_service')
    for parent in notebook_path.parents:
        if parent.name == "css-geodata-service":
            os.chdir(parent)
            break

    print(f"Working dir set to: {os.getcwd()}")


def get_working_directory() -> Path:
    set_notbook_wd()
    wd = Path().resolve()
    # print(f"wd {wd}")
    return wd


def get_roa_base_path() -> Path:
    base_path = get_working_directory() / "css_geodata_service" / "robustness_of_accessibility"
    # print(f"base path {base_path}")
    return base_path


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
