"""
Dynamic Time-Dependent Robustness of Accessibility (RoA) Simulation Engine.

Evaluates the time-dependent accessibility index RoA(t) and time-integrated
resilience metric RoA_Int across a multi-day disaster horizon (e.g. 14-day HQ100
flood event).

At each simulation hour t:
1. Identifies active facilities D_active(t) from the Dual-Resource FSM lifetime state.
2. Retrieves the pre-sliced passable road network graph G_k for the current flood stage.
3. Computes shortest paths c'(s_j, d_j'(t), t) from citizen origin sample locations
   to the nearest active facility in D_active(t).
4. Computes the aggregate accessibility ratio RoA(t) = sum( a_j * (c_base / c_disrupted) ).
5. Numerically integrates RoA(t) across the horizon to yield total crisis resilience RoA_Int.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd

from css_geodata_service.robustness_of_accessibility.utils.flood_interpolation import (
    build_stage_for_hour,
)
from css_geodata_service.robustness_of_accessibility.utils.stage_disruptions import (
    build_stage_graphs,
)

logger = logging.getLogger(__name__)


def compute_dynamic_roa(
    street_network: nx.MultiGraph | nx.MultiDiGraph,
    samples: gpd.GeoDataFrame,
    poi_gdfs: Dict[str, gpd.GeoDataFrame],
    backup_lifetime_results: Dict[str, List[Dict[str, Any]]],
    stage_disruptions: Dict[int, List[Tuple[int, int, int]]],
    stages: List[Dict],
    stage_for_hour: Optional[List[int]] = None,
    sample_weights: Optional[Dict[Any, float] | Sequence[float]] = None,
    active_states: Sequence[str] = ("Operational", "Depleting"),
    poi_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Compute the time-series RoA(t) curves and RoA_Int resilience metrics.

    Parameters
    ----------
    street_network : nx.MultiGraph or nx.MultiDiGraph
        Undisrupted base road network graph (undirected).
    samples : GeoDataFrame
        Citizen origin sample points (must contain ``osmid`` or node index).
    poi_gdfs : dict of GeoDataFrame
        ``{poi_type: gdf}`` containing destination POIs with ``nearest_node_id``
        column populated.
    backup_lifetime_results : dict
        Output from :func:`compute_backup_lifetime` containing hourly states
        ``state_by_hour`` for each facility.
    stage_disruptions : dict
        ``{stage_idx: flooded_edges}`` from :func:`load_or_compute_stage_disruptions`.
    stages : list of dict
        Discrete flood stages list.
    stage_for_hour : list of int, optional
        336-element mapping from simulation hour to stage index. Built from
        *stages* if omitted.
    sample_weights : sequence or dict, optional
        Weights a_j for each sample point (sums to 1.0). Uniform (1/m) if omitted.
    active_states : sequence of str
        FSM states considered functional destinations (default: Operational, Depleting).
    poi_weights : dict, optional
        Weights per POI type for combined RoA. Uniform across types if omitted.

    Returns
    -------
    dict
        Structured evaluation payload including hourly curves and integrated resilience.
    """
    if stage_for_hour is None:
        stage_for_hour = build_stage_for_hour(stages)

    n_hours = len(stage_for_hour)

    # 1. Extract sample node identifiers
    if "osmid" in samples.columns:
        sample_node_ids = samples["osmid"].tolist()
    else:
        sample_node_ids = samples.index.tolist()

    n_samples = len(sample_node_ids)
    if n_samples == 0:
        raise ValueError("samples GeoDataFrame contains 0 sample points.")

    # Uniform sample weights if not provided
    if sample_weights is None:
        weights_arr = np.full(n_samples, 1.0 / n_samples)
    elif isinstance(sample_weights, dict):
        weights_arr = np.array([sample_weights.get(sid, 1.0 / n_samples) for sid in sample_node_ids])
        weights_arr = weights_arr / weights_arr.sum()
    else:
        weights_arr = np.array(sample_weights, dtype=float)
        weights_arr = weights_arr / weights_arr.sum()

    # Uniform POI type weights if not provided
    poi_types = list(poi_gdfs.keys())
    if poi_weights is None:
        poi_weights = {ptype: 1.0 / len(poi_types) for ptype in poi_types}

    # 2. Pre-build stage graphs
    stage_graphs = build_stage_graphs(street_network, stage_disruptions)

    # 3. Compute baseline travel costs c_base(s) for each POI type on undisrupted network
    baseline_distances: Dict[str, Dict[Any, float]] = {}
    for ptype, gdf in poi_gdfs.items():
        if "nearest_node_id" not in gdf.columns:
            logger.info("Computing nearest_node_id for '%s' POIs...", ptype)
            rep_points = gdf.geometry.representative_point()
            gdf["nearest_node_id"] = [
                int(ox.distance.nearest_nodes(street_network, X=pt.x, Y=pt.y))
                for pt in rep_points
            ]
        sources = set(gdf["nearest_node_id"].unique())
        dist_base, _ = nx.multi_source_dijkstra(
            street_network, sources=sources, weight="length"
        )
        baseline_distances[ptype] = dist_base

    # 4. Hourly simulation loop
    dijkstra_cache: Dict[Tuple[int, Tuple[int, ...]], Dict[Any, float]] = {}
    roa_by_type: Dict[str, List[float]] = {ptype: [] for ptype in poi_types}
    roa_combined: List[float] = []
    hourly_active_count: Dict[str, List[int]] = {ptype: [] for ptype in poi_types}

    for t in range(n_hours):
        k = stage_for_hour[t]
        hourly_type_scores: Dict[str, float] = {}

        for ptype, gdf in poi_gdfs.items():
            # Identify active POI nearest node IDs for hour t
            active_node_ids: Set[int] = set()
            type_backup = backup_lifetime_results.get(ptype, [])

            for idx in range(len(gdf)):
                state = type_backup[idx]["state_by_hour"][t]
                if state in active_states:
                    active_node_ids.add(int(gdf.iloc[idx]["nearest_node_id"]))

            hourly_active_count[ptype].append(len(active_node_ids))

            if len(active_node_ids) > 0:
                cache_key = (k, tuple(sorted(active_node_ids)))
                if cache_key not in dijkstra_cache:
                    dist_t, _ = nx.multi_source_dijkstra(
                        stage_graphs[k], sources=active_node_ids, weight="length"
                    )
                    dijkstra_cache[cache_key] = dist_t
                else:
                    dist_t = dijkstra_cache[cache_key]
            else:
                dist_t = {}

            # Calculate individual sample accessibility ratios
            base_dists = baseline_distances[ptype]
            sample_ratios = np.zeros(n_samples, dtype=float)

            for s_idx, s_node in enumerate(sample_node_ids):
                cb = base_dists.get(s_node, float("inf"))
                ct = dist_t.get(s_node, float("inf"))

                if cb == float("inf") or ct == float("inf"):
                    ratio = 0.0
                elif ct == 0:
                    ratio = 1.0
                else:
                    ratio = min(1.0, cb / ct)

                sample_ratios[s_idx] = ratio

            # Aggregate RoA for this POI type at hour t
            type_roa_score = float(np.sum(sample_ratios * weights_arr))
            roa_by_type[ptype].append(type_roa_score)
            hourly_type_scores[ptype] = type_roa_score

        # Weighted combined RoA at hour t
        comb_score = sum(poi_weights[ptype] * hourly_type_scores[ptype] for ptype in poi_types)
        roa_combined.append(comb_score)

    # 5. Compute time-integrated resilience indices (trapezoidal rule / mean across horizon)
    roa_int_by_type: Dict[str, float] = {
        ptype: float(np.mean(roa_by_type[ptype])) for ptype in poi_types
    }
    roa_int_combined: float = float(np.mean(roa_combined))

    logger.info(
        "Dynamic RoA simulation finished across %d hours: Combined RoA_Int = %.2f%% (Peak = %.2f%%)",
        n_hours,
        roa_int_combined * 100,
        min(roa_combined) * 100,
    )

    return {
        "hours": list(range(n_hours)),
        "roa_by_type": roa_by_type,
        "roa_combined": roa_combined,
        "roa_int_by_type": roa_int_by_type,
        "roa_int_combined": roa_int_combined,
        "hourly_active_count": hourly_active_count,
        "total_facility_count": {ptype: len(gdf) for ptype, gdf in poi_gdfs.items()},
        "n_samples": n_samples,
    }


def load_or_compute_dynamic_roa(
    cache_dir: Path | str,
    street_network: nx.MultiGraph | nx.MultiDiGraph,
    samples: gpd.GeoDataFrame,
    poi_gdfs: Dict[str, gpd.GeoDataFrame],
    backup_lifetime_results: Dict[str, List[Dict[str, Any]]],
    stage_disruptions: Dict[int, List[Tuple[int, int, int]]],
    stages: List[Dict],
    place_name: str = "Trier, Germany",
    stage_for_hour: Optional[List[int]] = None,
    sample_weights: Optional[Dict[Any, float] | Sequence[float]] = None,
    active_states: Sequence[str] = ("Operational", "Depleting"),
    poi_weights: Optional[Dict[str, float]] = None,
    force_recompute: bool = False,
) -> Dict[str, Any]:
    """Load pre-computed dynamic RoA simulation results from cache, or compute and
    persist to disk.

    Parameters
    ----------
    cache_dir : Path or str
        Base cache directory (e.g. ``data/processed``).
    street_network : nx.MultiGraph or nx.MultiDiGraph
        Undisrupted base road network graph.
    samples : GeoDataFrame
        Citizen origin sample points.
    poi_gdfs : dict
        POIs GeoDataFrame dict.
    backup_lifetime_results : dict
        FSM backup simulation results.
    stage_disruptions : dict
        Stage disruptions dict.
    stages : list of dict
        Flood stage geometries.
    place_name : str
        Region identifier.
    stage_for_hour : list of int, optional
        Hourly stage schedule.
    sample_weights : sequence or dict, optional
        Sample point weights.
    active_states : sequence of str
        Functional POI states.
    poi_weights : dict, optional
        POI type weights.
    force_recompute : bool
        If True, ignore disk cache and recompute.

    Returns
    -------
    dict
        Complete dynamic RoA evaluation payload.
    """
    safe_place = place_name.replace(" ", "_").replace(",", "")
    n_stages = len(stages)
    n_hours = len(stage_for_hour) if stage_for_hour is not None else 336
    cache_file = (
        Path(cache_dir)
        / "roa"
        / f"roa_simulation_{safe_place}_{n_stages}_{n_hours}h.json"
    )

    if cache_file.exists() and not force_recompute:
        logger.info("Loading dynamic RoA simulation from cache %s", cache_file)
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    results = compute_dynamic_roa(
        street_network=street_network,
        samples=samples,
        poi_gdfs=poi_gdfs,
        backup_lifetime_results=backup_lifetime_results,
        stage_disruptions=stage_disruptions,
        stages=stages,
        stage_for_hour=stage_for_hour,
        sample_weights=sample_weights,
        active_states=active_states,
        poi_weights=poi_weights,
    )

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(results, f, separators=(",", ":"))

    logger.info("Dynamic RoA simulation results saved to cache %s", cache_file)
    return results
