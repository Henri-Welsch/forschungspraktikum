"""
Flood-status determination for individual facilities / infrastructure.

Determines, independently for each facility, whether its own geometry is
currently covered by the flood polygon. This is a purely geometric check:
it does not consider road accessibility, NCNN infrastructure relationships,
or downstream functional dependencies (e.g. a hospital losing power because
its nearest substation is flooded). Those are out of scope for this module
by design — see the flood-simulation work package this was built for.

Usage
-----
    from css_geodata_service.robustness_of_accessibility.utils.flood_status import (
        compute_flood_status,
        compute_flood_status_by_stage,
    )

    flooded = compute_flood_status(hospitals, flood_geometry)

    status_by_stage = compute_flood_status_by_stage(
        {"hospital": hospitals, "fire_station": fire_stations},
        stages,
    )
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)


def compute_flood_status(
    facilities: gpd.GeoDataFrame,
    flood_geometry: Optional[BaseGeometry],
) -> pd.Series:
    """Return a boolean Series (aligned with *facilities*' index): ``True``
    where a facility's own geometry is covered by *flood_geometry*.

    A facility is considered flooded when its geometry intersects the flood
    polygon. ``flood_geometry`` of ``None`` (or an empty geometry) means no
    flooding is present — every facility is reported as available.

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
    return facilities.geometry.intersects(flood_geometry)


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
