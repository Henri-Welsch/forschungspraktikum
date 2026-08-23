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
_code_dir = _current_dir.parent if _current_dir.name == "scripts" else _current_dir
_project_root = _code_dir.parent

if str(_code_dir) not in sys.path:
    sys.path.insert(0, str(_code_dir))

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd

from css_geodata_service.robustness_of_accessibility.utils.dynamic_roa import (
    load_or_compute_multi_tier_bundle,
    TIER_CONFIGS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MultiTierEval")


def run_all_tiers():
    proc_dir = _code_dir / "css_geodata_service" / "robustness_of_accessibility" / "data" / "processed"
    hazard_dir = _code_dir / "css_geodata_service" / "robustness_of_accessibility" / "data" / "input" / "Flooding" / "HazardAreas"
    hq_raw_dir = hazard_dir / "HQ_raw"
    place_name = "Trier, Germany"

    bundle = load_or_compute_multi_tier_bundle(
        cache_dir=proc_dir,
        hq_raw_dir=hq_raw_dir,
        place_name=place_name,
        force_recompute=True,
    )

    results_summary = {}
    for tier_key, tier_data in bundle["tiers"].items():
        tier_roa = tier_data["roa"]
        min_roa = min(tier_roa["roa_combined"])
        roa_int = tier_roa["roa_int_combined"]
        hosp_min = min(tier_roa["roa_by_type"]["hospital"])
        fire_min = min(tier_roa["roa_by_type"]["fire_station"])

        results_summary[tier_key] = {
            "title": tier_data["title"],
            "RoA_Int": roa_int,
            "RoA_min": min_roa,
            "Hospital_min": hosp_min,
            "Fire_min": fire_min,
        }

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
