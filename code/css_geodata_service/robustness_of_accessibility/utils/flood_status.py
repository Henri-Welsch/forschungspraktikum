"""
Flood-status determination for individual facilities / infrastructure.

Determines, independently for each facility, whether its own geometry is
currently covered by the flood polygon. This is a purely geometric check:
``compute_flood_status`` / ``compute_flood_status_by_stage`` do not consider
road accessibility, NCNN infrastructure relationships, or downstream
functional dependencies — they only ever look at a facility's own geometry.

Built on top of that, ``resolve_connection_targets`` /
``compute_dependency_status_by_stage`` add one further, still purely
rule-based, layer: cascading failure via the NCNN POI→infrastructure
connections computed elsewhere (see ``utils/ncnn.py``). A hospital or fire
station becomes *dead* when either (a) its own geometry is flooded, or
(b) the power or water station it is connected to (via NCNN) is itself
flooded. No other downstream effects (e.g. disrupted road accessibility)
are modelled here.

Usage
-----
    from css_geodata_service.robustness_of_accessibility.utils.flood_status import (
        compute_flood_status,
        compute_flood_status_by_stage,
        compute_dependency_status_by_stage,
    )

    flooded = compute_flood_status(hospitals, flood_geometry)

    direct_status = compute_flood_status_by_stage(
        {"hospital": hospitals, "fire_station": fire_stations,
         "power": power_stations, "water": water_stations},
        stages,
    )

    dependency_status = compute_dependency_status_by_stage(
        infrastructure_gdfs={"power": power_stations, "water": water_stations},
        connections=ncnn_results,   # {"hospital": {"power": gdf, "water": gdf}, ...}
        direct_flooded_by_stage=direct_status,
    )
    hospital_dead_by_stage = dependency_status["hospital"]["dead_by_stage"]
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import geopandas as gpd
import pandas as pd
from shapely import wkt
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)


def compute_flood_status(
    facilities: gpd.GeoDataFrame,
    flood_geometry: Optional[BaseGeometry],
) -> pd.Series:
    """Return a boolean Series (aligned with *facilities*' index): ``True``
    where a facility's representative point is covered by *flood_geometry*.

    A facility is considered flooded when a single representative point of
    its geometry falls inside the flood polygon — not when any part of its
    (possibly very large) footprint merely touches it. This deliberately
    trades a small chance of under-reporting a partial flood for avoiding
    false positives where a sliver of the flood polygon clips the edge of a
    large campus/footprint while the building itself is unaffected; it also
    keeps the flood decision consistent with the map, which only ever draws
    a single point marker per facility. ``representative_point()`` (not
    ``centroid``) is used because the centroid of a concave footprint can
    fall outside the polygon entirely. ``flood_geometry`` of ``None`` (or an
    empty geometry) means no flooding is present — every facility is
    reported as available.

    Parameters
    ----------
    facilities : GeoDataFrame
        Facility / infrastructure features (hospitals, fire stations, power
        stations, water stations …). Uses each row's own ``geometry`` —
        polygon footprints and points are both supported.
    flood_geometry : BaseGeometry or None
        The current flood polygon (same CRS as *facilities*, EPSG:4326 for
        the stages produced by ``flood_interpolation``), or ``None`` for no
        flooding.

    Returns
    -------
    pandas.Series of bool
        Indexed like *facilities*. ``True`` = flooded / unavailable.
    """
    if flood_geometry is None or flood_geometry.is_empty or len(facilities) == 0:
        return pd.Series(False, index=facilities.index)
    return facilities.geometry.representative_point().intersects(flood_geometry)


def compute_flood_status_by_stage(
    facility_gdfs: Dict[str, gpd.GeoDataFrame],
    stages: List[Dict],
) -> Dict[str, List[List[bool]]]:
    """For every pre-computed flood *stage*, determine which facilities of
    each type are flooded.

    Parameters
    ----------
    facility_gdfs : dict
        ``{facility_type: GeoDataFrame}``, e.g.
        ``{"hospital": ..., "fire_station": ..., "power": ..., "water": ...}``.
    stages : list of dict
        Stage list as returned by
        :func:`css_geodata_service.robustness_of_accessibility.utils.flood_interpolation.load_or_compute_flood_stages`.
        Each entry has keys ``stage_index``, ``progress``, ``geojson``
        (a GeoJSON geometry dict, or ``None`` for a no-flood stage).

    Returns
    -------
    dict
        ``{facility_type: [[bool, ...], ...]}`` — one boolean list per stage
        (in stage order), each of length ``len(facility_gdfs[facility_type])``.
        Index ``i`` in each inner list corresponds to the ``i``-th row of the
        respective facility GeoDataFrame.
    """
    results: Dict[str, List[List[bool]]] = {ftype: [] for ftype in facility_gdfs}

    for stage in stages:
        geojson_geom = stage.get("geojson")
        flood_geometry = shape(geojson_geom) if geojson_geom is not None else None

        for ftype, gdf in facility_gdfs.items():
            flooded = compute_flood_status(gdf, flood_geometry)
            results[ftype].append(flooded.tolist())

    logger.info(
        "Flood status computed for %d stage(s), facility types: %s",
        len(stages), list(facility_gdfs.keys()),
    )
    return results


def resolve_connection_targets(
    connections: gpd.GeoDataFrame,
    infrastructure: gpd.GeoDataFrame,
) -> List[Optional[int]]:
    """Resolve each NCNN connection row to the row it connects to in
    *infrastructure*.

    ``ncnn.calculate_ncnn_routes`` records, per POI, the *road node* the
    nearest infrastructure feature was snapped to (``infra_position``, a WKT
    point) rather than a direct reference back to a row in the original
    infrastructure GeoDataFrame. This function recovers that reference by
    finding the infrastructure row whose own centroid is nearest to that
    snapped node — which is, by construction, almost always the exact
    feature NCNN matched (multiple features would only be ambiguous if two
    physically distinct stations snapped to the very same road node, in
    which case NCNN's own nearest-neighbor result is itself ambiguous).

    Parameters
    ----------
    connections : GeoDataFrame
        One NCNN result, e.g. ``ncnn_results["hospital"]["power"]`` — one row
        per POI, with an ``infra_position`` WKT column.
    infrastructure : GeoDataFrame
        The infrastructure features *connections* was computed against, e.g.
        ``power_stations``. Must be the same feature set/order used for the
        NCNN computation.

    Returns
    -------
    list of (int or None)
        Aligned with *connections* rows. Each entry is the positional
        (``iloc``) index into *infrastructure*, or ``None`` where the
        connection has no resolvable target (no route was found).
    """
    # Reproject to a metric CRS (UTM 32N, already used elsewhere in this
    # package for distance computations) so "nearest" is measured in metres
    # rather than distorted lon/lat degrees — infra stations can legitimately
    # sit close together in real data.
    infra_centroids_m = infrastructure.geometry.to_crs("EPSG:25832").centroid
    targets: List[Optional[int]] = []

    for _, row in connections.iterrows():
        pos_wkt = row.get("infra_position")
        if pos_wkt is None or (isinstance(pos_wkt, float) and pd.isna(pos_wkt)) or len(infra_centroids_m) == 0:
            targets.append(None)
            continue
        point_m = gpd.GeoSeries([wkt.loads(pos_wkt)], crs="EPSG:4326").to_crs("EPSG:25832").iloc[0]
        distances = infra_centroids_m.distance(point_m)
        targets.append(int(distances.values.argmin()))

    return targets


def compute_dependency_status_by_stage(
    infrastructure_gdfs: Dict[str, gpd.GeoDataFrame],
    connections: Dict[str, Dict[str, gpd.GeoDataFrame]],
    direct_flooded_by_stage: Dict[str, List[List[bool]]],
) -> Dict[str, Dict]:
    """Combine direct flooding with NCNN power/water dependencies to
    determine full POI availability across all flood stages.

    A POI (hospital, fire station, …) becomes *dead* at a given stage when
    **either**:

    1. its own geometry is covered by that stage's flood polygon
       (``direct_flooded_by_stage[poi_type][stage]``), **or**
    2. the infrastructure station it is connected to via NCNN — for any
       infrastructure type present in *connections* — is itself flooded at
       that stage.

    This models cascading infrastructure failure (e.g. "hospital loses
    power because its nearest substation is flooded") without touching road
    accessibility: the POI→infrastructure assignment comes entirely from
    the pre-computed, static NCNN routes, and "is the connected station
    flooded" is answered purely geometrically via *direct_flooded_by_stage*.
    A connection whose target cannot be resolved (no NCNN route found) is
    never treated as a cause of failure — it simply does not contribute.

    Parameters
    ----------
    infrastructure_gdfs : dict
        ``{infra_type: GeoDataFrame}``, e.g.
        ``{"power": power_stations, "water": water_stations}`` — the exact
        feature sets the NCNN connections were computed against.
    connections : dict
        ``{poi_type: {infra_type: GeoDataFrame}}`` — NCNN results as
        returned by
        :func:`css_geodata_service.robustness_of_accessibility.utils.ncnn.load_or_calculate_ncnn_routes`.
    direct_flooded_by_stage : dict
        Output of :func:`compute_flood_status_by_stage`, covering *both*
        the POI types in *connections* and the infrastructure types in
        *infrastructure_gdfs* (i.e. computed with a combined
        ``facility_gdfs`` containing all of them).

    Returns
    -------
    dict
        ``{poi_type: {
            "dead_by_stage": [[bool, ...], ...],   # combined status, aligned
                                                     # with the POI GeoDataFrame
                                                     # the connections were built from
            "connections": {
                infra_type: {
                    "target_index": [int or None, ...],   # aligned with POI rows
                    "dead_by_stage": [[bool, ...], ...],  # this connection's own status
                }
                for infra_type in connections[poi_type]
            },
        } for poi_type in connections}``
    """
    results: Dict[str, Dict] = {}

    for poi_type, infra_connections in connections.items():
        connection_info: Dict[str, Dict] = {}

        for infra_type, conn_gdf in infra_connections.items():
            infra_gdf = infrastructure_gdfs[infra_type]
            target_index = resolve_connection_targets(conn_gdf, infra_gdf)
            infra_flooded_by_stage = direct_flooded_by_stage[infra_type]

            conn_dead_by_stage: List[List[bool]] = [
                [
                    bool(stage_flooded[idx]) if idx is not None else False
                    for idx in target_index
                ]
                for stage_flooded in infra_flooded_by_stage
            ]
            connection_info[infra_type] = {
                "target_index": target_index,
                "dead_by_stage": conn_dead_by_stage,
            }

        n_stages = len(direct_flooded_by_stage[poi_type])
        dead_by_stage: List[List[bool]] = []
        for stage_i in range(n_stages):
            combined = list(direct_flooded_by_stage[poi_type][stage_i])
            for info in connection_info.values():
                conn_dead = info["dead_by_stage"][stage_i]
                combined = [c or d for c, d in zip(combined, conn_dead)]
            dead_by_stage.append(combined)

        results[poi_type] = {
            "dead_by_stage": dead_by_stage,
            "connections": connection_info,
        }

    logger.info(
        "Dependency status computed for POI types: %s (infra types: %s)",
        list(connections.keys()), list(infrastructure_gdfs.keys()),
    )
    return results


def load_or_compute_flood_status_by_stage(
    cache_dir: Path,
    facility_gdfs: Dict[str, gpd.GeoDataFrame],
    stages: List[Dict],
    place_name: str = "Trier, Germany",
    force_recompute: bool = False,
) -> Dict[str, List[List[bool]]]:
    """Cached wrapper around :func:`compute_flood_status_by_stage`.

    Stores JSON results under ``cache_dir / "flood_status" /``.
    File name pattern::

        direct_flood_status_{safe_place}_{n_stages}.json

    Parameters
    ----------
    cache_dir : Path
        Processed-data directory (e.g. ``data/processed/``).
    facility_gdfs : dict
        ``{facility_type: GeoDataFrame}``
    stages : list of dict
        Pre-computed flood stages list.
    place_name : str
        Embedded in cache-file names to avoid collisions across cities.
    force_recompute : bool
        Ignore existing cache files and recompute.

    Returns
    -------
    dict
        ``{facility_type: [[bool, ...], ...]}``
    """
    status_cache_dir = cache_dir / "flood_status"
    status_cache_dir.mkdir(parents=True, exist_ok=True)

    safe_place = place_name.replace(", ", "_").replace(" ", "_")
    cache_file = status_cache_dir / f"direct_flood_status_{safe_place}_{len(stages)}.json"

    if cache_file.exists() and not force_recompute:
        logger.info("Direct flood status: loading cached results from %s", cache_file)
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    results = compute_flood_status_by_stage(facility_gdfs=facility_gdfs, stages=stages)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info("Direct flood status: results cached to %s", cache_file)
    return results


def load_or_compute_dependency_status_by_stage(
    cache_dir: Path,
    infrastructure_gdfs: Dict[str, gpd.GeoDataFrame],
    connections: Dict[str, Dict[str, gpd.GeoDataFrame]],
    direct_flooded_by_stage: Dict[str, List[List[bool]]],
    place_name: str = "Trier, Germany",
    force_recompute: bool = False,
) -> Dict[str, Dict]:
    """Cached wrapper around :func:`compute_dependency_status_by_stage`.

    Stores JSON results under ``cache_dir / "flood_status" /``.
    File name pattern::

        dependency_status_{safe_place}_{n_stages}.json

    Parameters
    ----------
    cache_dir : Path
        Processed-data directory (e.g. ``data/processed/``).
    infrastructure_gdfs : dict
        ``{infra_type: GeoDataFrame}``
    connections : dict
        ``{poi_type: {infra_type: GeoDataFrame}}``
    direct_flooded_by_stage : dict
        Output of direct flood status calculation.
    place_name : str
        Embedded in cache-file names to avoid collisions across cities.
    force_recompute : bool
        Ignore existing cache files and recompute.

    Returns
    -------
    dict
        ``{poi_type: {"dead_by_stage": ..., "connections": ...}}``
    """
    status_cache_dir = cache_dir / "flood_status"
    status_cache_dir.mkdir(parents=True, exist_ok=True)

    safe_place = place_name.replace(", ", "_").replace(" ", "_")
    first_type_stages = next(iter(direct_flooded_by_stage.values())) if direct_flooded_by_stage else []
    n_stages = len(first_type_stages)
    cache_file = status_cache_dir / f"dependency_status_{safe_place}_{n_stages}.json"

    if cache_file.exists() and not force_recompute:
        logger.info("Dependency status: loading cached results from %s", cache_file)
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    results = compute_dependency_status_by_stage(
        infrastructure_gdfs=infrastructure_gdfs,
        connections=connections,
        direct_flooded_by_stage=direct_flooded_by_stage,
    )
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info("Dependency status: results cached to %s", cache_file)
    return results
