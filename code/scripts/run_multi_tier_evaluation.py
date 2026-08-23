"""
Multi-Tier RoA Simulation Runner & Pre-Caching Pipeline.

Runs all 4 evaluation tiers (Level A, Level B1, Level B2, Level C) across the
336-hour hydrodynamic flood hydrograph (29 HQ_raw stages).
Caches all simulation results into data/processed/roa/ and data/processed/backup_lifetime/.
"""
import json
import logging
from pathlib import Path
import sys

# --- Dynamic Path & Environment Resolution ---
_current_dir = Path(__file__).resolve().parent
_code_dir = None
_project_root = None

_cursor = _current_dir
while _cursor != _cursor.parent:
    if (_cursor / "code").is_dir() and (_cursor / "HQ_raw").is_dir():
        _project_root = _cursor
        _code_dir = _cursor / "code"
        break
    if _cursor.name == "code" and (_cursor.parent / "HQ_raw").is_dir():
        _code_dir = _cursor
        _project_root = _cursor.parent
        break
    _cursor = _cursor.parent

if _code_dir is None:
    _code_dir = _current_dir.parent if _current_dir.name == "scripts" else _current_dir
    _project_root = _code_dir.parent

if str(_code_dir) not in sys.path:
    sys.path.insert(0, str(_code_dir))

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd

from css_geodata_service.robustness_of_accessibility.utils.flood_interpolation import (
    load_or_compute_hq_raw_flood_stages,
    build_stage_for_hour,
)
from css_geodata_service.robustness_of_accessibility.utils.flood_status import (
    load_or_compute_flood_status_by_stage,
    load_or_compute_dependency_status_by_stage,
)
from css_geodata_service.robustness_of_accessibility.utils.ncnn import (
    load_or_calculate_ncnn_routes,
)
from css_geodata_service.robustness_of_accessibility.utils.backup_lifetime import (
    compute_backup_lifetime,
)
from css_geodata_service.robustness_of_accessibility.utils.stage_disruptions import (
    load_or_compute_stage_disruptions,
)
from css_geodata_service.robustness_of_accessibility.utils.dynamic_roa import (
    compute_dynamic_roa,
)
from css_geodata_service.robustness_of_accessibility.robustness_of_accessibility import (
    load_or_draw_sample,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MultiTierEval")

# Parameter Configurations for All 4 Tiers
TIER_CONFIGS = {
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

def run_all_tiers():
    proc_dir = _code_dir / "css_geodata_service" / "robustness_of_accessibility" / "data" / "processed"
    hq_raw_dir = _project_root / "HQ_raw"
    place_name = "Trier, Germany"
    safe_place = "Trier_Germany"

    # 1. Load Road Graph
    logger.info("Loading road network graph...")
    graph_path = proc_dir / "network" / f"drive_graph_{place_name}.graphml"
    road_network = ox.load_graphml(graph_path)
    road_network_undirected = ox.utils_graph.get_undirected(road_network) if road_network.is_directed() else road_network

    # 2. Load Boundary & Samples
    logger.info("Loading study boundary and origin samples...")
    boundary_gdf = gpd.read_file(proc_dir / f"services/boundary_geom_{place_name}.geojson")
    boundary_geom = boundary_gdf.geometry.iloc[0]

    nodes_gdf = ox.graph_to_gdfs(road_network_undirected, edges=False)
    samples = load_or_draw_sample(
        cache_path=proc_dir / f"samples/samples_{place_name}_500.geojson",
        polygon=boundary_geom,
        gdf_nodes_drive_service_graph=nodes_gdf,
        number_total_samples=500,
        random_seed=42,
    )
    logger.info("Samples count: %d", len(samples))

    # 3. Load POIs & Infrastructure
    logger.info("Loading POIs and utility infrastructure...")
    hospitals = gpd.read_file(proc_dir / f"services/hospitals_{place_name}.geojson")
    fire_stations = gpd.read_file(proc_dir / f"services/fire_stations_{place_name}.geojson")
    power_substations = gpd.read_file(proc_dir / f"services/power_substations_{place_name}.geojson")
    power_plants = gpd.read_file(proc_dir / f"services/power_plants_{place_name}.geojson")
    water_works = gpd.read_file(proc_dir / f"services/water_works_{place_name}.geojson")
    water_towers = gpd.read_file(proc_dir / f"services/water_towers_{place_name}.geojson")
    pumping_stations = gpd.read_file(proc_dir / f"services/pumping_stations_{place_name}.geojson")

    power_stations = pd.concat([power_substations, power_plants], ignore_index=True)
    water_stations = pd.concat([water_works, water_towers, pumping_stations], ignore_index=True)

    poi_gdfs = {"hospital": hospitals, "fire_station": fire_stations}
    infrastructure_gdfs = {"power": power_stations, "water": water_stations}
    facility_gdfs = {**poi_gdfs, **infrastructure_gdfs}

    for ptype, gdf in poi_gdfs.items():
        if "nearest_node_id" not in gdf.columns:
            rep_points = gdf.geometry.representative_point()
            gdf["nearest_node_id"] = [
                int(ox.distance.nearest_nodes(road_network_undirected, X=pt.x, Y=pt.y))
                for pt in rep_points
            ]

    # 4. Load Hydrodynamic Flood Stages (29 stages from HQ_raw)
    logger.info("Loading hydrodynamic flood stages from %s...", hq_raw_dir)
    stages = load_or_compute_hq_raw_flood_stages(
        cache_dir=proc_dir,
        hq_raw_dir=hq_raw_dir,
        place_name=place_name,
        min_gauge_m=9.00,
        max_gauge_m=11.80,
    )
    stage_for_hour = build_stage_for_hour(stages)
    n_hours = len(stage_for_hour)

    # 5. Load Stage Disruptions
    logger.info("Loading road stage disruptions...")
    stage_disruptions = load_or_compute_stage_disruptions(
        cache_dir=proc_dir,
        street_network=road_network_undirected,
        stages=stages,
        place_name=place_name,
    )

    # 6. Load Direct Flood Status & NCNN Dependency Status
    logger.info("Loading facility flood status and NCNN dependency status...")
    facility_flood_status = load_or_compute_flood_status_by_stage(
        cache_dir=proc_dir,
        facility_gdfs=facility_gdfs,
        stages=stages,
        place_name=place_name,
    )

    ncnn_results = load_or_calculate_ncnn_routes(
        cache_dir=proc_dir,
        poi_gdfs=poi_gdfs,
        infrastructure_gdfs=infrastructure_gdfs,
        street_network=road_network_undirected,
        place_name=place_name,
    )

    dependency_status = load_or_compute_dependency_status_by_stage(
        cache_dir=proc_dir,
        infrastructure_gdfs=infrastructure_gdfs,
        connections=ncnn_results,
        direct_flooded_by_stage=facility_flood_status,
        place_name=place_name,
    )

    # 7. Run Simulation for Each Tier
    results_summary = {}
    multi_tier_payload = {
        "hours": list(range(n_hours)),
        "stages_count": len(stages),
        "n_samples": len(samples),
        "tiers": {},
    }

    for tier_key, tier_meta in TIER_CONFIGS.items():
        logger.info("\n" + "=" * 60)
        logger.info(">>> RUNNING %s: %s", tier_key, tier_meta["title"])
        logger.info("=" * 60)

        # A. Compute Backup Lifetime FSM for this tier
        tier_backup = compute_backup_lifetime(
            poi_types=["hospital", "fire_station"],
            direct_flooded_by_stage=facility_flood_status,
            dependency_status=dependency_status,
            stage_for_hour=stage_for_hour,
            backup_cfg=tier_meta["backup_cfg"],
            restart_threshold=0.15,
        )

        # Cache backup lifetime JSON
        backup_out_dir = proc_dir / "backup_lifetime"
        backup_out_dir.mkdir(parents=True, exist_ok=True)
        backup_tier_path = backup_out_dir / f"backup_lifetime_{safe_place}_{tier_key}_{n_hours}h.json"
        with open(backup_tier_path, "w", encoding="utf-8") as f:
            json.dump(tier_backup, f)

        # B. Compute Dynamic RoA Simulation for this tier
        tier_roa = compute_dynamic_roa(
            street_network=road_network_undirected,
            samples=samples,
            poi_gdfs=poi_gdfs,
            backup_lifetime_results=tier_backup,
            stage_disruptions=stage_disruptions,
            stages=stages,
            stage_for_hour=stage_for_hour,
            poi_weights={"hospital": 0.5, "fire_station": 0.5},
        )

        # Cache RoA JSON
        roa_out_dir = proc_dir / "roa"
        roa_out_dir.mkdir(parents=True, exist_ok=True)
        roa_tier_path = roa_out_dir / f"roa_{safe_place}_{tier_key}_{n_hours}h.json"
        with open(roa_tier_path, "w", encoding="utf-8") as f:
            json.dump(tier_roa, f)

        # Store in multi-tier payload
        multi_tier_payload["tiers"][tier_key] = {
            "title": tier_meta["title"],
            "description": tier_meta["description"],
            "backup_lifetime": tier_backup,
            "roa": tier_roa,
        }

        min_roa = min(tier_roa["roa_combined"])
        roa_int = tier_roa["roa_int_combined"]
        hosp_min = min(tier_roa["roa_by_type"]["hospital"])
        fire_min = min(tier_roa["roa_by_type"]["fire_station"])

        results_summary[tier_key] = {
            "title": tier_meta["title"],
            "RoA_Int": roa_int,
            "RoA_min": min_roa,
            "Hospital_min": hosp_min,
            "Fire_min": fire_min,
        }
        logger.info(
            "Finished %s: RoA_Int = %.2f%% | Peak Nadir RoA = %.2f%% (Hosp = %.2f%%, Fire = %.2f%%)",
            tier_key, roa_int * 100, min_roa * 100, hosp_min * 100, fire_min * 100
        )

    # Cache combined multi-tier bundle
    bundle_path = proc_dir / "roa" / f"multi_tier_roa_bundle_{safe_place}_{n_hours}h.json"
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(multi_tier_payload, f)
    logger.info("Saved combined multi-tier bundle to: %s", bundle_path)

    print("\n" + "=" * 80)
    print("                 MULTI-TIER EVALUATION RESULTS SUMMARY                    ")
    print("=" * 80)
    print(f"{'Tier':10s} | {'Title':30s} | {'RoA_Int':>9s} | {'RoA_min':>9s} | {'Hosp_min':>9s} | {'Fire_min':>9s}")
    print("-" * 85)
    for t_k, s in results_summary.items():
        print(
            f"{t_k:10s} | {s['title']:30s} | {s['RoA_Int']*100:8.2f}% | "
            f"{s['RoA_min']*100:8.2f}% | {s['Hospital_min']*100:8.2f}% | {s['Fire_min']*100:8.2f}%"
        )
    print("=" * 80)

if __name__ == "__main__":
    run_all_tiers()
