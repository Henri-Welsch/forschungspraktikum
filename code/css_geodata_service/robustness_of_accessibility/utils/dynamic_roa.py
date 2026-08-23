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
from shapely.ops import unary_union

from css_geodata_service.robustness_of_accessibility.robustness_of_accessibility import (
    load_or_draw_sample,
)
from css_geodata_service.robustness_of_accessibility.utils.backup_lifetime import (
    compute_backup_lifetime,
)
from css_geodata_service.robustness_of_accessibility.utils.flood_interpolation import (
    build_stage_for_hour,
    load_or_compute_hq_raw_flood_stages,
)
from css_geodata_service.robustness_of_accessibility.utils.flood_status import (
    load_or_compute_dependency_status_by_stage,
    load_or_compute_flood_status_by_stage,
)
from css_geodata_service.robustness_of_accessibility.utils.ncnn import (
    load_or_calculate_ncnn_routes,
)
from css_geodata_service.robustness_of_accessibility.utils.stage_disruptions import (
    build_stage_graphs,
    load_or_compute_stage_disruptions,
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
    peak_stage_idx = max(stage_for_hour)
    peak_hour = int(np.where(np.array(stage_for_hour) == peak_stage_idx)[0][0])
    peak_sample_ratios_by_type: Dict[str, List[float]] = {}

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

            if t == peak_hour:
                peak_sample_ratios_by_type[ptype] = sample_ratios.tolist()

            # Aggregate RoA for this POI type at hour t
            type_roa_score = float(np.sum(sample_ratios * weights_arr))
            roa_by_type[ptype].append(type_roa_score)
            hourly_type_scores[ptype] = type_roa_score

        # Weighted combined RoA at hour t
        comb_score = sum(poi_weights[ptype] * hourly_type_scores[ptype] for ptype in poi_types)
        roa_combined.append(comb_score)

    peak_sample_ratios_combined = [
        float(sum(poi_weights[ptype] * peak_sample_ratios_by_type[ptype][i] for ptype in poi_types))
        for i in range(n_samples)
    ]

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
        "peak_hour": peak_hour,
        "peak_sample_ratios_by_type": peak_sample_ratios_by_type,
        "peak_sample_ratios_combined": peak_sample_ratios_combined,
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


# Parameter Configurations for All 4 Resilience Tiers
TIER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "Level_A": {
        "title": "Level A: Road Baseline",
        "description": "Road cuts active; facilities permanent (infinite utility buffers).",
        "backup_cfg": {
            "hospital": {
                "recharge_delay": 0,
                "resources": {
                    "power": {"capacity": float("inf"), "loss_rate": 0.0, "gain_rate": 1.0},
                    "water": {"capacity": float("inf"), "loss_rate": 0.0, "gain_rate": 1.0},
                },
            },
            "fire_station": {
                "recharge_delay": 0,
                "resources": {
                    "power": {"capacity": float("inf"), "loss_rate": 0.0, "gain_rate": 1.0},
                    "water": {"capacity": float("inf"), "loss_rate": 0.0, "gain_rate": 1.0},
                },
            },
        },
    },
    "Level_B1": {
        "title": "Level B1: Power Cascade",
        "description": "Substation flooding & power depletion; water unconstrained.",
        "backup_cfg": {
            "hospital": {
                "recharge_delay": 48,
                "resources": {
                    "power": {"capacity": 72, "loss_rate": 1.0, "gain_rate": 1.0},
                    "water": {"capacity": float("inf"), "loss_rate": 0.0, "gain_rate": 1.0},
                },
            },
            "fire_station": {
                "recharge_delay": 24,
                "resources": {
                    "power": {"capacity": 24, "loss_rate": 1.0, "gain_rate": 1.0},
                    "water": {"capacity": float("inf"), "loss_rate": 0.0, "gain_rate": 1.0},
                },
            },
        },
    },
    "Level_B2": {
        "title": "Level B2: Water Cascade",
        "description": "Water source flooding & tank depletion; power unconstrained.",
        "backup_cfg": {
            "hospital": {
                "recharge_delay": 48,
                "resources": {
                    "power": {"capacity": float("inf"), "loss_rate": 0.0, "gain_rate": 1.0},
                    "water": {"capacity": 48, "loss_rate": 1.0, "gain_rate": 1.0},
                },
            },
            "fire_station": {
                "recharge_delay": 24,
                "resources": {
                    "power": {"capacity": float("inf"), "loss_rate": 0.0, "gain_rate": 1.0},
                    "water": {"capacity": 12, "loss_rate": 1.0, "gain_rate": 1.0},
                },
            },
        },
    },
    "Level_C": {
        "title": "Level C: Compound Failure",
        "description": "Simultaneous power + water cascades with reboot delay and hysteresis.",
        "backup_cfg": {
            "hospital": {
                "recharge_delay": 48,
                "resources": {
                    "power": {"capacity": 72, "loss_rate": 1.0, "gain_rate": 1.0},
                    "water": {"capacity": 48, "loss_rate": 1.0, "gain_rate": 1.0},
                },
            },
            "fire_station": {
                "recharge_delay": 24,
                "resources": {
                    "power": {"capacity": 24, "loss_rate": 1.0, "gain_rate": 1.0},
                    "water": {"capacity": 12, "loss_rate": 1.0, "gain_rate": 1.0},
                },
            },
        },
    },
}


def load_or_compute_multi_tier_bundle(
    cache_dir: Path | str,
    street_network: Optional[nx.MultiGraph | nx.MultiDiGraph] = None,
    samples: Optional[gpd.GeoDataFrame] = None,
    poi_gdfs: Optional[Dict[str, gpd.GeoDataFrame]] = None,
    infrastructure_gdfs: Optional[Dict[str, gpd.GeoDataFrame]] = None,
    stages: Optional[List[Dict]] = None,
    stage_for_hour: Optional[List[int]] = None,
    facility_flood_status: Optional[Dict[str, Dict[int, List[str]]]] = None,
    dependency_status: Optional[Dict[str, Any]] = None,
    stage_disruptions: Optional[Dict[int, List[Tuple[int, int, int]]]] = None,
    place_name: str = "Trier, Germany",
    hq_raw_dir: Optional[Path | str] = None,
    tier_configs: Optional[Dict[str, Dict[str, Any]]] = None,
    force_recompute: bool = False,
) -> Dict[str, Any]:
    """Load pre-computed multi-tier RoA simulation bundle (Levels A, B1, B2, C)
    from cache, or compute and cache all tiers automatically.

    Parameters
    ----------
    cache_dir : Path or str
        Base cache directory (e.g. ``data/processed``).
    street_network : nx.MultiGraph or nx.MultiDiGraph, optional
        Base undisrupted road graph. Automatically loaded/fetched if None.
    samples : GeoDataFrame, optional
        Citizen origin sample points. Automatically loaded/drawn if None.
    poi_gdfs : dict of GeoDataFrame, optional
        ``{poi_type: gdf}`` containing destination POIs.
    infrastructure_gdfs : dict of GeoDataFrame, optional
        ``{infra_type: gdf}`` containing utility infrastructure.
    stages : list of dict, optional
        Discrete flood stages list. Automatically computed from ``hq_raw_dir`` if None.
    stage_for_hour : list of int, optional
        336-element mapping from hour to stage index.
    facility_flood_status : dict, optional
        Direct flood status per facility type and stage.
    dependency_status : dict, optional
        NCNN dependency status per facility type and stage.
    stage_disruptions : dict, optional
        Flooded edges per flood stage.
    place_name : str
        Region identifier (default: "Trier, Germany").
    hq_raw_dir : Path or str, optional
        Path to raw hydrodynamic gauge stage directory (``HQ_raw``).
    tier_configs : dict, optional
        Dictionary mapping tier keys to configuration metadata. Defaults to :data:`TIER_CONFIGS`.
    force_recompute : bool
        If True, ignore cached files and recompute all 4 simulation tiers.

    Returns
    -------
    dict
        Master multi-tier simulation payload containing results for all 4 tiers.
    """
    cache_dir = Path(cache_dir)
    safe_place = place_name.replace(" ", "_").replace(",", "")
    n_hours = len(stage_for_hour) if stage_for_hour is not None else 336
    bundle_path = cache_dir / "roa" / f"multi_tier_roa_bundle_{safe_place}_{n_hours}h.json"

    if bundle_path.exists() and not force_recompute:
        logger.info("Loading pre-computed multi-tier simulation bundle from %s", bundle_path)
        with open(bundle_path, "r", encoding="utf-8") as f:
            return json.load(f)

    logger.info("Multi-tier bundle not cached or recompute requested. Starting self-healing computation pipeline...")

    # 1. Resolve HQ_raw directory
    if hq_raw_dir is None:
        p = cache_dir.resolve()
        for candidate in [p, *p.parents]:
            if (candidate / "HQ_raw").is_dir():
                hq_raw_dir = candidate / "HQ_raw"
                break
            if (candidate.parent / "HQ_raw").is_dir():
                hq_raw_dir = candidate.parent / "HQ_raw"
                break
        if hq_raw_dir is None:
            hq_raw_dir = Path.cwd().parent / "HQ_raw"
    hq_raw_dir = Path(hq_raw_dir)

    # 2. Resolve Flood Stages
    if stages is None:
        logger.info("Loading hydrodynamic flood stages from %s...", hq_raw_dir)
        stages = load_or_compute_hq_raw_flood_stages(
            cache_dir=cache_dir,
            hq_raw_dir=hq_raw_dir,
            place_name=place_name,
            min_gauge_m=9.00,
            max_gauge_m=11.80,
        )

    if stage_for_hour is None:
        stage_for_hour = build_stage_for_hour(stages)
    n_hours = len(stage_for_hour)

    # 3. Resolve Road Network Graph
    if street_network is None:
        graph_path = cache_dir / "network" / f"drive_graph_{place_name}.graphml"
        if graph_path.exists():
            road_network = ox.load_graphml(graph_path)
        else:
            logger.info("Fetching road network from OSM for '%s'...", place_name)
            road_network = ox.graph_from_place(place_name, network_type="drive")
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            ox.save_graphml(road_network, graph_path)
        street_network = ox.utils_graph.get_undirected(road_network) if road_network.is_directed() else road_network
    elif street_network.is_directed():
        street_network = ox.utils_graph.get_undirected(street_network)

    # 4. Resolve Study Boundary
    boundary_cache = cache_dir / f"services/boundary_geom_{place_name}.geojson"
    if boundary_cache.exists():
        boundary_gdf = gpd.read_file(boundary_cache)
        boundary_geom = unary_union(boundary_gdf.geometry)
    else:
        logger.info("Fetching study boundary from OSM for '%s'...", place_name)
        place_gdf = ox.geocode_to_gdf(place_name)
        boundary_geom = unary_union(place_gdf.geometry)
        boundary_cache.parent.mkdir(parents=True, exist_ok=True)
        gpd.GeoDataFrame(geometry=[boundary_geom], crs="EPSG:4326").to_file(
            boundary_cache, driver="GeoJSON"
        )

    # 5. Resolve Samples
    if samples is None:
        nodes_gdf = ox.graph_to_gdfs(street_network, edges=False)
        samples = load_or_draw_sample(
            cache_path=cache_dir / f"samples/samples_{place_name}_500.geojson",
            polygon=boundary_geom,
            gdf_nodes_drive_service_graph=nodes_gdf,
            number_total_samples=500,
            random_seed=42,
        )

    # 6. Resolve POIs & Infrastructure
    services_cache_dir = cache_dir / "services"
    services_cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_feature_gdf(feat_name: str, osm_tags: dict) -> gpd.GeoDataFrame:
        feat_path = services_cache_dir / f"{feat_name}_{place_name}.geojson"
        if feat_path.exists():
            return gpd.read_file(feat_path)
        logger.info("Fetching '%s' from OSM...", feat_name)
        gdf = ox.features_from_polygon(polygon=boundary_geom, tags=osm_tags)
        gdf.to_file(feat_path, driver="GeoJSON")
        return gdf

    if poi_gdfs is None:
        hospitals = _get_feature_gdf("hospitals", {"amenity": ["hospital"]})
        fire_stations = _get_feature_gdf("fire_stations", {"amenity": ["fire_station"]})
        poi_gdfs = {"hospital": hospitals, "fire_station": fire_stations}

    if infrastructure_gdfs is None:
        power_substations = _get_feature_gdf("power_substations", {"power": ["substation"]})
        power_plants = _get_feature_gdf("power_plants", {"power": ["plant"]})
        water_works = _get_feature_gdf("water_works", {"man_made": ["water_works"]})
        water_towers = _get_feature_gdf("water_towers", {"man_made": ["water_tower"]})
        pumping_stations = _get_feature_gdf("pumping_stations", {"man_made": ["pumping_station"]})

        power_stations = gpd.GeoDataFrame(
            pd.concat([power_substations, power_plants], ignore_index=True), crs="EPSG:4326"
        )
        water_stations = gpd.GeoDataFrame(
            pd.concat([water_works, water_towers, pumping_stations], ignore_index=True), crs="EPSG:4326"
        )
        infrastructure_gdfs = {"power": power_stations, "water": water_stations}

    facility_gdfs = {**poi_gdfs, **infrastructure_gdfs}

    # Ensure nearest_node_id populated on POIs
    for ptype, gdf in poi_gdfs.items():
        if "nearest_node_id" not in gdf.columns:
            rep_points = gdf.geometry.representative_point()
            gdf["nearest_node_id"] = [
                int(ox.distance.nearest_nodes(street_network, X=pt.x, Y=pt.y))
                for pt in rep_points
            ]

    # 7. Resolve Stage Disruptions
    if stage_disruptions is None:
        stage_disruptions = load_or_compute_stage_disruptions(
            cache_dir=cache_dir,
            street_network=street_network,
            stages=stages,
            place_name=place_name,
        )

    # 8. Resolve Direct Flood Status & NCNN Dependency Status
    if facility_flood_status is None:
        facility_flood_status = load_or_compute_flood_status_by_stage(
            cache_dir=cache_dir,
            facility_gdfs=facility_gdfs,
            stages=stages,
            place_name=place_name,
        )

    if dependency_status is None:
        ncnn_results = load_or_calculate_ncnn_routes(
            cache_dir=cache_dir,
            poi_gdfs=poi_gdfs,
            infrastructure_gdfs=infrastructure_gdfs,
            street_network=street_network,
            place_name=place_name,
        )
        dependency_status = load_or_compute_dependency_status_by_stage(
            cache_dir=cache_dir,
            infrastructure_gdfs=infrastructure_gdfs,
            connections=ncnn_results,
            direct_flooded_by_stage=facility_flood_status,
            place_name=place_name,
        )

    # 9. Execute All Simulation Tiers
    configs = tier_configs if tier_configs is not None else TIER_CONFIGS
    backup_out_dir = cache_dir / "backup_lifetime"
    backup_out_dir.mkdir(parents=True, exist_ok=True)
    roa_out_dir = cache_dir / "roa"
    roa_out_dir.mkdir(parents=True, exist_ok=True)

    multi_tier_payload = {
        "hours": list(range(n_hours)),
        "stages_count": len(stages),
        "n_samples": len(samples),
        "tiers": {},
    }

    for tier_key, tier_meta in configs.items():
        logger.info("Computing simulation for %s: %s...", tier_key, tier_meta["title"])

        tier_backup = compute_backup_lifetime(
            poi_types=["hospital", "fire_station"],
            direct_flooded_by_stage=facility_flood_status,
            dependency_status=dependency_status,
            stage_for_hour=stage_for_hour,
            backup_cfg=tier_meta["backup_cfg"],
            restart_threshold=0.15,
        )

        backup_tier_path = backup_out_dir / f"backup_lifetime_{safe_place}_{tier_key}_{n_hours}h.json"
        with open(backup_tier_path, "w", encoding="utf-8") as f:
            json.dump(tier_backup, f)

        tier_roa = compute_dynamic_roa(
            street_network=street_network,
            samples=samples,
            poi_gdfs=poi_gdfs,
            backup_lifetime_results=tier_backup,
            stage_disruptions=stage_disruptions,
            stages=stages,
            stage_for_hour=stage_for_hour,
            poi_weights={"hospital": 0.5, "fire_station": 0.5},
        )

        roa_tier_path = roa_out_dir / f"roa_{safe_place}_{tier_key}_{n_hours}h.json"
        with open(roa_tier_path, "w", encoding="utf-8") as f:
            json.dump(tier_roa, f)

        multi_tier_payload["tiers"][tier_key] = {
            "title": tier_meta["title"],
            "description": tier_meta["description"],
            "backup_lifetime": tier_backup,
            "roa": tier_roa,
        }

    # Save master combined bundle
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(multi_tier_payload, f)

    logger.info("Multi-tier simulation bundle successfully cached to %s", bundle_path)
    return multi_tier_payload
