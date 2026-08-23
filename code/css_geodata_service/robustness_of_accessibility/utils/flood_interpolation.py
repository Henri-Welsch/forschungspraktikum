"""
Flood stage interpolation for the RoA flood simulation.

Approximates intermediate inundation extents between no-flood (progress=0.0)
and the full HQ100 inundation (progress=1.0) by progressively eroding the
HQ100 flood polygon inward.

Physical interpretation
-----------------------
Riverine flooding begins at the lowest-lying terrain immediately adjacent
to the river channel and expands laterally as water levels rise.  Geometrically,
the last areas to remain inundated are those furthest from the flood-polygon
boundary — the "core" of the flood zone.  A negative Shapely buffer removes a
strip of fixed width from the polygon perimeter, isolating this inner core.

For flood progress p ∈ [0, 1]:
  - p = 0.0  →  maximum erosion  →  empty polygon  (no flooding)
  - p = 1.0  →  zero erosion     →  full HQ100 polygon (peak flooding)

Algorithm
---------
1. Merge all HQ100 source polygons via ``unary_union``.
2. Reproject to EPSG:25832 (UTM 32N) so all distances are in metres.
3. Apply ``simplify(simplify_m)`` to reduce the vertex count for efficient
   buffer computation and compact GeoJSON storage.
4. Binary-search for ``max_erosion_m``: the minimum inward buffer that
   produces an empty geometry.
5. For each stage index ``i`` in ``[0, n_stages)``, compute progress
   ``p = i / (n_stages - 1)`` and apply buffer ``−max_erosion_m * (1 − p)``.
6. Reproject each stage polygon back to EPSG:4326 and apply a final
   coordinate-space simplification to reduce GeoJSON output size.
7. Persist all stages as a GeoJSON FeatureCollection::

       {cache_dir}/flood_interpolation/flood_stages_{safe_place}_{n_stages}.geojson

Usage
-----
    from css_geodata_service.robustness_of_accessibility.utils.flood_interpolation import (
        load_or_compute_flood_stages,
        get_stage_for_progress,
        compute_hourly_flood_progress,
    )

    stages = load_or_compute_flood_stages(
        cache_dir=Path("data/processed"),
        hq100_gdf=hazard_area_gdf,
        n_stages=50,
        place_name="Trier, Germany",
    )

    # Flood level for simulation hour 100 (day 5, 04:00)
    progress = compute_hourly_flood_progress(100)

    # Nearest pre-computed stage for that progress value
    stage = get_stage_for_progress(stages, progress)
    print(stage["progress"], stage["geojson"])
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import geopandas as gpd
import pandas as pd
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simulation schedule
# ---------------------------------------------------------------------------

#: Total simulation length in hours (14 days × 24 h).
SIMULATION_HOURS: int = 336

# Phase boundary hours (0-indexed, half-open intervals).
_RISE_START: int = 48    # End of pre-event phase;  start of rising water (day 3).
_RISE_END: int = 144     # End of rising phase;      96 h linear ramp (day 6 → 7).
_PEAK_END: int = 192     # End of HQ100 peak phase;  48 h at full extent (day 8 → 9).
_FALL_END: int = 288     # End of recession phase;   96 h symmetric ramp (day 12 → 13).
# Hours [288, 335] are post-event (no flooding).


def compute_hourly_flood_progress(hour: int) -> float:
    """Return the flood level [0, 1] for simulation *hour* (0-indexed).

    The 14-day, 336-hour scenario is symmetric and split into five phases:

    +----------+---------+-------+-----------------------------------------+
    | Hours    | Days    | Level | Phase                                   |
    +==========+=========+=======+=========================================+
    |   0 –  47|  1 –  2 | 0.0   | Pre-event — no flooding                 |
    +----------+---------+-------+-----------------------------------------+
    |  48 – 143|  3 –  6 | 0→1   | Rising water (96 h linear ramp)         |
    +----------+---------+-------+-----------------------------------------+
    | 144 – 191|  7 –  8 | 1.0   | HQ100 peak flood                        |
    +----------+---------+-------+-----------------------------------------+
    | 192 – 287|  9 – 12 | 1→0   | Receding water (96 h symmetric ramp)    |
    +----------+---------+-------+-----------------------------------------+
    | 288 – 335| 13 – 14 | 0.0   | Post-event — no flooding                |
    +----------+---------+-------+-----------------------------------------+

    Parameters
    ----------
    hour : int
        Simulation hour in ``[0, 335]``.

    Returns
    -------
    float
        Flood progress in ``[0.0, 1.0]``.

    Raises
    ------
    ValueError
        If *hour* is outside ``[0, 335]``.
    """
    if not (0 <= hour < SIMULATION_HOURS):
        raise ValueError(
            f"hour must be in [0, {SIMULATION_HOURS - 1}], got {hour}"
        )

    if hour < _RISE_START:
        return 0.0
    elif hour < _RISE_END:
        return (hour - _RISE_START) / (_RISE_END - _RISE_START)
    elif hour < _PEAK_END:
        return 1.0
    elif hour < _FALL_END:
        elapsed = hour - _PEAK_END
        duration = _FALL_END - _PEAK_END
        return 1.0 - elapsed / duration
    else:
        return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_or_compute_flood_stages(
    cache_dir: Path,
    hq100_gdf: gpd.GeoDataFrame,
    n_stages: int = 50,
    place_name: str = "Trier, Germany",
    force_recalculate: bool = False,
) -> List[Dict]:
    """Load or compute *n_stages* intermediate flood inundation polygons.

    Each stage represents a discrete fraction of the full HQ100 flood extent,
    interpolated via progressive inward erosion (negative Shapely buffer).
    Results are cached as a GeoJSON FeatureCollection for fast re-use across
    sessions.

    File name pattern::

        {cache_dir}/flood_interpolation/flood_stages_{safe_place}_{n_stages}.geojson

    Parameters
    ----------
    cache_dir : Path
        Processed-data directory (``data/processed/``).
    hq100_gdf : GeoDataFrame
        GeoDataFrame containing the HQ100 flood polygon(s) in EPSG:4326.
    n_stages : int
        Number of discrete flood levels to pre-compute (default 50).
        More stages → smoother animation; fewer stages → faster startup.
    place_name : str
        Embedded in the cache-file name to avoid collisions across cities.
    force_recalculate : bool
        Ignore any existing cache file and always recompute.

    Returns
    -------
    list of dict
        One entry per stage.  Each entry has keys:

        * ``"stage_index"``  – int, counter ``0 … n_stages-1``
        * ``"progress"``     – float, flood level ``0.0 … 1.0``
        * ``"geojson"``      – dict (GeoJSON geometry) or ``None`` (no flood)
    """
    flood_cache_dir = cache_dir / "flood_interpolation"
    flood_cache_dir.mkdir(parents=True, exist_ok=True)

    safe_place = place_name.replace(", ", "_").replace(" ", "_")
    cache_file = (
        flood_cache_dir / f"flood_stages_{safe_place}_{n_stages}.geojson"
    )

    if cache_file.exists() and not force_recalculate:
        logger.info(
            "Flood stages: loading %d cached stages from %s",
            n_stages, cache_file,
        )
        return _load_stages_from_cache(cache_file)

    logger.info(
        "Flood stages: computing %d stages for '%s' …",
        n_stages, place_name,
    )
    stages = _compute_flood_stages(hq100_gdf, n_stages)
    _save_stages_to_cache(stages, cache_file)
    logger.info(
        "Flood stages: %d stages persisted to %s",
        n_stages, cache_file,
    )
    return stages


def load_or_compute_hq_raw_flood_stages(
    cache_dir: Path,
    hq_raw_dir: Path | str,
    place_name: str = "Trier, Germany",
    min_gauge_m: float = 9.0,
    max_gauge_m: float = 11.80,
    simplify_tolerance: float = 0.0001,
    force_recalculate: bool = False,
) -> List[Dict]:
    """Load or pre-process hydrodynamic flood stage polygons from raw gauge GeoJSONs (HQ_raw).

    Discovers discrete gauge stages (e.g. 9.00 m to 11.80 m in 10 cm steps), aligns CRS to
    EPSG:4326, applies light coordinate simplification for compact storage and smooth rendering,
    and caches the stage collection to disk.

    The current provided data inside ``HQ_raw`` covers the Trier, Germany area and includes
    gauge heights starting at 9.00 m and goes up to a maximum of 12.70 m.

    Parameters
    ----------
    cache_dir : Path
        Processed-data directory (``data/processed/``).
    hq_raw_dir : Path or str
        Path to directory containing raw gauge GeoJSON files (e.g. `HQ_raw`).
    place_name : str
        Embedded in the cache-file name to avoid collisions across cities.
    min_gauge_m : float
        Minimum gauge height to include (default: 9.0 m).
    max_gauge_m : float
        Maximum gauge height to include (default: 11.80 m for HQ100).
    simplify_tolerance : float
        Coordinate simplification in degrees (default 0.0001 ≈ 7m).
    force_recalculate : bool
        If True, ignore cache and recompute.

    Returns
    -------
    list of dict
        Stage entries with keys ``stage_index``, ``progress``, ``geojson``, ``gauge_height_m``.
    """
    raw_dir = Path(hq_raw_dir).resolve()
    if not raw_dir.exists():
        raise FileNotFoundError(f"HQ_raw directory not found: {raw_dir}")

    all_files = sorted(list(raw_dir.glob("*.geojson")))
    matched_files: List[Tuple[float, Path]] = []
    for f in all_files:
        match = re.search(r"Pegel_(\d{2})_(\d{2})m", f.name)
        if match:
            h = float(f"{match.group(1)}.{match.group(2)}")
            if min_gauge_m <= h <= max_gauge_m:
                matched_files.append((h, f))

    matched_files.sort(key=lambda x: x[0])
    n_stages = len(matched_files)
    if n_stages == 0:
        raise ValueError(
            f"No gauge GeoJSON files found in {raw_dir} between {min_gauge_m}m and {max_gauge_m}m."
        )

    flood_cache_dir = Path(cache_dir) / "flood_interpolation"
    flood_cache_dir.mkdir(parents=True, exist_ok=True)
    safe_place = place_name.replace(", ", "_").replace(" ", "_")
    cache_file = flood_cache_dir / f"flood_stages_hq_raw_{safe_place}_{n_stages}.geojson"

    if cache_file.exists() and not force_recalculate:
        logger.info(
            "HQ_raw flood stages: loading %d cached stages from %s",
            n_stages, cache_file,
        )
        return _load_stages_from_cache(cache_file)

    logger.info(
        "HQ_raw flood stages: processing %d stages (%.2fm to %.2fm) from %s …",
        n_stages, min_gauge_m, max_gauge_m, raw_dir,
    )

    stages: List[Dict] = []
    for i, (gauge_m, fpath) in enumerate(matched_files):
        progress = i / (n_stages - 1) if n_stages > 1 else 1.0
        gdf = gpd.read_file(fpath)
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        union_geom = unary_union(gdf.geometry)
        if union_geom.is_empty:
            geojson_geom = None
        else:
            if simplify_tolerance > 0:
                union_geom = union_geom.simplify(simplify_tolerance, preserve_topology=True)
            raw_json = json.loads(gpd.GeoSeries([union_geom], crs="EPSG:4326").to_json())
            geojson_geom = raw_json["features"][0]["geometry"]

        stages.append({
            "stage_index": i,
            "progress": round(progress, 6),
            "geojson": geojson_geom,
            "gauge_height_m": gauge_m,
        })

    _save_stages_to_cache(stages, cache_file)
    logger.info(
        "HQ_raw flood stages: %d stages persisted to %s",
        n_stages, cache_file,
    )
    return stages


def build_stage_for_hour(stages: List[Dict]) -> List[int]:
    """Map every simulation hour ``[0, SIMULATION_HOURS)`` to the index into
    *stages* whose progress is nearest that hour's flood level.

    Shared by :func:`build_flood_animation_html` (to pick which flood polygon
    to draw each frame) and anything that needs to expand other per-stage
    data — e.g. facility flood/dependency status — to hourly resolution
    (see ``utils.backup_lifetime.compute_backup_lifetime``), so both stay
    using the exact same stage at any given hour.
    """
    return [
        get_stage_for_progress(stages, compute_hourly_flood_progress(h))["stage_index"]
        for h in range(SIMULATION_HOURS)
    ]


def get_stage_for_progress(
    stages: List[Dict],
    progress: float,
) -> Dict:
    """Return the stage entry whose progress value is nearest to *progress*.

    Parameters
    ----------
    stages : list of dict
        Stage list returned by :func:`load_or_compute_flood_stages`.
    progress : float
        Target flood level in ``[0, 1]``.

    Returns
    -------
    dict
        Stage entry with the nearest progress value.
    """
    return min(stages, key=lambda s: abs(s["progress"] - progress))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_max_erosion_m(geometry_m: BaseGeometry, tol_m: float = 2.0) -> float:
    """Binary-search for the smallest negative buffer that empties *geometry_m*.

    Parameters
    ----------
    geometry_m : BaseGeometry
        Geometry in a metric CRS (buffer distance unit = metres).
    tol_m : float
        Convergence tolerance in metres (default 2 m).

    Returns
    -------
    float
        Minimum erosion distance (metres) that makes the geometry empty.
    """
    # Initial upper bound: at least half the square root of the area, at
    # least 100 m.  Doubled until the geometry is emptied.
    lo, hi = 0.0, max(geometry_m.area ** 0.5 / 2.0, 100.0)

    while not geometry_m.buffer(-hi).is_empty:
        hi *= 2.0
        if hi > 1_000_000:
            raise RuntimeError(
                "max_erosion search diverged — is the geometry in a metric CRS?"
            )

    while hi - lo > tol_m:
        mid = (lo + hi) / 2.0
        if geometry_m.buffer(-mid).is_empty:
            hi = mid
        else:
            lo = mid

    logger.debug("Max erosion: %.1f m  (tolerance %.1f m)", lo, tol_m)
    return lo


def _interpolate_stage(
    geometry_m: BaseGeometry,
    max_erosion_m: float,
    progress: float,
) -> Optional[BaseGeometry]:
    """Apply inward erosion to *geometry_m* corresponding to *progress*.

    Parameters
    ----------
    geometry_m : BaseGeometry
        Base flood polygon in a metric CRS.
    max_erosion_m : float
        Maximum erosion distance in metres (empties the geometry at p=0).
    progress : float
        Flood level in ``[0, 1]``.

    Returns
    -------
    BaseGeometry or None
        Eroded geometry, or ``None`` when the result is empty (no flood).
    """
    if progress <= 0.0:
        return None
    if progress >= 1.0:
        return geometry_m

    erosion = max_erosion_m * (1.0 - progress)
    result = geometry_m.buffer(-erosion)
    return None if result.is_empty else result


def _compute_flood_stages(
    hq100_gdf: gpd.GeoDataFrame,
    n_stages: int,
    simplify_m: float = 25.0,
) -> List[Dict]:
    """Compute *n_stages* flood polygons from no-flood to full HQ100 extent.

    Parameters
    ----------
    hq100_gdf : GeoDataFrame
        HQ100 flood polygons in EPSG:4326.
    n_stages : int
        Number of discrete stages to produce.
    simplify_m : float
        Douglas–Peucker simplification tolerance in metres applied to the
        unified polygon *before* buffering.  Reduces vertex count for faster
        processing and more compact GeoJSON output.  Default is 25 m.

    Returns
    -------
    list of dict
        Stage entries with keys ``stage_index``, ``progress``, ``geojson``.
    """
    # ------------------------------------------------------------------
    # 1. Project to metric CRS and build simplified union
    # ------------------------------------------------------------------
    gdf_m = hq100_gdf.to_crs("EPSG:25832")
    union_m = unary_union(gdf_m.geometry)
    union_simplified = union_m.simplify(simplify_m, preserve_topology=True)
    logger.debug(
        "HQ100 union: %.0f m² (original) → simplified at %.0f m tolerance",
        union_m.area, simplify_m,
    )

    # ------------------------------------------------------------------
    # 2. Determine the maximum inward erosion distance
    # ------------------------------------------------------------------
    max_erosion_m = _find_max_erosion_m(union_simplified)
    logger.info(
        "Flood stages: max erosion %.1f m, computing %d stages …",
        max_erosion_m, n_stages,
    )

    # ------------------------------------------------------------------
    # 3. Compute each stage
    # ------------------------------------------------------------------
    stages: List[Dict] = []
    for i in range(n_stages):
        progress = i / (n_stages - 1) if n_stages > 1 else 1.0

        geom_m = _interpolate_stage(union_simplified, max_erosion_m, progress)

        if geom_m is None:
            geojson_geom = None
        else:
            # Reproject back to WGS-84
            geom_4326 = (
                gpd.GeoDataFrame(geometry=[geom_m], crs="EPSG:25832")
                .to_crs("EPSG:4326")
                .geometry.iloc[0]
            )
            # Light coordinate-space simplification to keep the GeoJSON compact
            # (~0.0001° ≈ 7 m at Trier's latitude — acceptable for display).
            geom_display = geom_4326.simplify(0.0001, preserve_topology=True)
            # Extract the geometry dict directly (no wrapping FeatureCollection)
            raw = json.loads(
                gpd.GeoSeries([geom_display], crs="EPSG:4326").to_json()
            )
            geojson_geom = raw["features"][0]["geometry"]

        stages.append({
            "stage_index": i,
            "progress": round(progress, 6),
            "geojson": geojson_geom,
        })

        if (i + 1) % 10 == 0 or i + 1 == n_stages:
            logger.debug("  Stage %d / %d  (progress %.3f)", i + 1, n_stages, progress)

    return stages


def _save_stages_to_cache(stages: List[Dict], cache_file: Path) -> None:
    """Persist *stages* as a GeoJSON FeatureCollection.

    Each Feature stores stage metadata in ``properties`` and the interpolated
    flood polygon (or ``null``) as ``geometry``.
    """
    features = []
    for s in stages:
        props = {
            "stage_index": s["stage_index"],
            "progress":    s["progress"],
        }
        if "gauge_height_m" in s:
            props["gauge_height_m"] = s["gauge_height_m"]
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": s["geojson"],   # GeoJSON geometry dict or null
        })
    fc = {"type": "FeatureCollection", "features": features}
    with cache_file.open("w", encoding="utf-8") as fh:
        json.dump(fc, fh, separators=(",", ":"))


def _load_stages_from_cache(cache_file: Path) -> List[Dict]:
    """Load stages from a GeoJSON FeatureCollection cache file."""
    with cache_file.open("r", encoding="utf-8") as fh:
        fc = json.load(fh)

    stages = []
    for feat in fc["features"]:
        props = feat["properties"]
        entry = {
            "stage_index": int(props["stage_index"]),
            "progress":    float(props["progress"]),
            "geojson":     feat.get("geometry"),    # None for no-flood stages
        }
        if "gauge_height_m" in props:
            entry["gauge_height_m"] = float(props["gauge_height_m"])
        stages.append(entry)
    return stages


# ---------------------------------------------------------------------------
# HTML animation builder
# ---------------------------------------------------------------------------

#: Leaflet.js CDN version pinned for reproducible offline caching.
_LEAFLET_VERSION = "1.9.4"

#: Self-contained HTML template for the flood simulation animation.
#: Placeholders: __BOUNDARY__, __FLOOD_STAGES__, __STAGE_HOURS__,
#: __CENTER_LAT__, __CENTER_LON__  — replaced by the builder function.
_ANIMATION_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trier Flood Simulation &mdash; 14-Day HQ100 Event</title>
  <link rel="stylesheet"
        href="https://unpkg.com/leaflet@{lv}/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@{lv}/dist/leaflet.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #13131f; font-family: 'Segoe UI', Arial, sans-serif; }}
    #map {{ width: 100%; height: calc(100vh - 145px); }}

    /* ── Control panel ─────────────────────────────────────── */
    #controls {{
      position: fixed; bottom: 0; left: 0; right: 0; height: 145px;
      background: rgba(15,15,32,0.97); border-top: 2px solid #2a2a4a;
      padding: 9px 18px 10px; color: #d8ddf0; z-index: 2000;
    }}

    /* Phase bar */
    #phase-bar {{
      display: flex; height: 9px; border-radius: 4px; overflow: hidden;
      margin-bottom: 6px;
    }}
    .phase-seg {{ height: 100%; transition: filter 0.08s; }}
    .phase-seg.active {{ filter: brightness(1.55) saturate(1.3); }}

    /* Time display */
    #time-display {{
      text-align: center; font-size: 15px; font-weight: 600;
      letter-spacing: 0.4px; margin-bottom: 7px; color: #e8ecff;
    }}

    /* Slider */
    #time-slider {{
      width: 100%; cursor: pointer; accent-color: #4d88ff;
      margin-bottom: 9px; display: block;
    }}

    /* Button row */
    #btn-row {{
      display: flex; align-items: center; gap: 7px; justify-content: center;
    }}
    .ctrl-btn {{
      background: #1e2040; border: 1px solid #3a3a6a; color: #d8ddf0;
      padding: 5px 13px; border-radius: 5px; cursor: pointer; font-size: 13px;
      transition: background 0.13s;
    }}
    .ctrl-btn:hover {{ background: #2a2d5a; }}
    #btn-play {{
      min-width: 90px; background: #173080; border-color: #4d88ff;
    }}
    #btn-play:hover {{ background: #1f3fa0; }}
    #speed-label {{ font-size: 12px; color: #8899cc; }}
    #speed-select {{
      background: #1e2040; border: 1px solid #3a3a6a; color: #d8ddf0;
      padding: 4px 8px; border-radius: 5px; font-size: 13px; cursor: pointer;
    }}

    /* ── Floating Overlay Columns ────────────────────────────── */
    .panel-column {{
      position: fixed; top: 10px; z-index: 1000;
      display: flex; flex-direction: column; gap: 8px;
      pointer-events: none;
    }}
    .panel-column > * {{
      pointer-events: auto; box-sizing: border-box; width: 100%;
    }}
    .panel-column-left {{
      left: 10px; width: 250px;
    }}
    .panel-column-right {{
      right: 10px; width: 260px;
    }}

    /* Info badge */
    #info-badge {{
      background: rgba(8,8,22,0.88); color: #c8d0f0;
      padding: 8px 14px; border-radius: 6px; font-size: 13px;
      line-height: 1.7; pointer-events: none;
    }}

    /* ── Facility / infrastructure panel ─────────────────────── */
    #facility-panel {{
      background: rgba(8,8,22,0.90); color: #d8ddf0;
      padding: 10px 14px; border-radius: 6px; font-size: 12.5px;
      line-height: 1.6;
      display: none;
    }}
    .facility-row {{ display: flex; align-items: center; gap: 7px; }}
    .facility-swatch {{
      width: 10px; height: 10px; border-radius: 50%;
      display: inline-block; flex-shrink: 0;
      border: 1px solid rgba(255,255,255,0.4);
    }}
    .facility-label {{ flex: 1; }}
    .facility-count {{ font-weight: 600; color: #e8ecff; }}
    .panel-subheader {{
      margin: 7px 0 3px; font-size: 10.5px; text-transform: uppercase;
      letter-spacing: 0.5px; color: #8899cc; border-top: 1px solid rgba(255,255,255,0.15);
      padding-top: 6px;
    }}
    .connection-swatch {{
      width: 16px; height: 3px; border-radius: 2px;
      display: inline-block; flex-shrink: 0;
    }}
    .backup-state-counts {{
      display: flex; gap: 7px; margin-top: 2px; font-size: 11px; color: #b8c0e0;
    }}
    .backup-state-counts b {{ color: #fff; }}

    /* ── Backup-lifetime markers (hospitals, fire stations, ...) ──────────── */
    @keyframes backup-blink {{ 0%, 49% {{ opacity: 1; }} 50%, 100% {{ opacity: 0.15; }} }}
    .backup-blinking {{ animation: backup-blink 1s steps(1) infinite; }}
    .backup-marker-icon {{ background: none; border: none; }}

    #backup-panel {{
      background: rgba(8,8,22,0.92); color: #d8ddf0;
      padding: 10px 14px; border-radius: 6px; font-size: 12.5px;
      line-height: 1.6;
      display: none;
    }}
    #backup-panel h4 {{ margin: 0 0 8px 0; font-size: 13px; color: #e8ecff; }}
    #backup-panel .row {{ display: flex; justify-content: space-between; margin: 4px 0; gap: 8px; }}
    #backup-panel .hint {{ color: #8899cc; font-style: italic; margin-top: 6px; }}

    /* ── RoA Resilience Panel ────────────────────────────────── */
    #roa-panel {{
      background: rgba(8,8,22,0.92); color: #d8ddf0;
      padding: 10px 14px; border-radius: 6px; font-size: 12.5px;
      line-height: 1.5;
      display: none; border: 1px solid rgba(77, 136, 255, 0.25);
    }}
    .roa-badge {{
      background: #173080; border: 1px solid #4d88ff;
      padding: 1px 6px; border-radius: 4px; font-size: 11px; color: #fff;
      font-weight: 600;
    }}
    .roa-chart-box {{
      margin-top: 6px; width: 100%; height: 38px;
      background: #0b0b18; border: 1px solid #23233c;
      border-radius: 4px; overflow: hidden; position: relative;
    }}
  </style>
</head>
<body>
  <div id="map"></div>

  <div class="panel-column panel-column-left">
    <div id="info-badge">
      <strong>Trier &mdash; Flood Simulation</strong><br>
      HQ100 &bull; 14-day event &bull; hourly steps
    </div>

    <div id="roa-panel">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">
        <span style="font-weight:600; color:#4d88ff; font-size:12px;">Accessibility (RoA)</span>
        <span class="roa-badge" id="roa-int-badge">RoA_Int: --%</span>
      </div>
      <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:4px;">
        <span style="color:#8899cc; font-size:12px;">Current:</span>
        <span id="roa-current-val" style="font-size:16px; font-weight:700; color:#50e3c2;">100.0%</span>
      </div>
      <div id="roa-type-breakdown" style="font-size:11px; color:#a0a8cc; margin-bottom:4px;">
        <div style="display:flex; justify-content:space-between;">
          <span>Hospitals:</span> <span id="roa-hosp-val" style="color:#e8ecff; font-weight:600;">100.0%</span>
        </div>
        <div style="display:flex; justify-content:space-between;">
          <span>Fire Stations:</span> <span id="roa-fire-val" style="color:#e8ecff; font-weight:600;">100.0%</span>
        </div>
      </div>
      <div class="roa-chart-box">
        <svg id="roa-svg-chart" viewBox="0 0 336 38" preserveAspectRatio="none" style="width:100%; height:100%; display:block;">
          <path id="roa-svg-fill" d="" fill="rgba(77, 136, 255, 0.18)" />
          <path id="roa-svg-line" d="" fill="none" stroke="#4d88ff" stroke-width="1.6" />
          <line id="roa-playhead" x1="0" y1="0" x2="0" y2="38" stroke="#50e3c2" stroke-width="2" />
        </svg>
      </div>
    </div>
  </div>

  <div class="panel-column panel-column-right">
    <div id="facility-panel"></div>
    <div id="backup-panel"></div>
  </div>

  <div id="controls">
    <!-- Phase indicator bar -->
    <div id="phase-bar">
      <div class="phase-seg" id="seg-pre"
           style="width:14.3%; background:#4a4a6a;"></div>
      <div class="phase-seg" id="seg-rise"
           style="width:28.6%; background:#d4820e;"></div>
      <div class="phase-seg" id="seg-peak"
           style="width:14.3%; background:#c03030;"></div>
      <div class="phase-seg" id="seg-fall"
           style="width:28.6%; background:#2855b0;"></div>
      <div class="phase-seg" id="seg-post"
           style="width:14.3%; background:#4a4a6a;"></div>
    </div>

    <div id="time-display">Day 1 &mdash; 00:00 &nbsp;|&nbsp; Pre-event</div>

    <input type="range" id="time-slider" min="0" max="335" value="0" step="1">

    <div id="btn-row">
      <button class="ctrl-btn" id="btn-prev-day" title="−1 day (↓)">◀ &minus;1d</button>
      <button class="ctrl-btn" id="btn-prev"     title="−1 hour (←)">⏮ &minus;1h</button>
      <button class="ctrl-btn" id="btn-play">&#9654; Play</button>
      <button class="ctrl-btn" id="btn-next"     title="+1 hour (→)">+1h ⏭</button>
      <button class="ctrl-btn" id="btn-next-day" title="+1 day (↑)">+1d ▶</button>
      <span id="speed-label">Speed:</span>
      <select id="speed-select">
        <option value="1">1 fps</option>
        <option value="4">4 fps</option>
        <option value="8" selected>8 fps</option>
        <option value="16">16 fps</option>
        <option value="24">24 fps</option>
      </select>
    </div>
  </div>

  <script>
    // ── Embedded simulation data ─────────────────────────────────────────────
    // Array of GeoJSON geometry dicts (or null for no-flood stages)
    var FLOOD_STAGES = __FLOOD_STAGES__;

    // Maps each simulation hour [0,335] to the index into FLOOD_STAGES
    var STAGE_FOR_HOUR = __STAGE_HOURS__;

    // WGS-84 boundary polygon geometry
    var BOUNDARY = __BOUNDARY__;

    // Facility / infrastructure layers (optional — empty when not provided)
    var FACILITY_TYPES   = __FACILITY_TYPES__;    // [{{key,label,color}}, ...]
    var FACILITY_POINTS  = __FACILITY_POINTS__;   // {{key: [{{lat,lon,name}}, ...]}}
    var FACILITY_FLOODED = __FACILITY_FLOODED__;  // {{key: [[bool, ...] per stage]}}

    // NCNN POI→infrastructure connections (optional — empty when not provided)
    var CONNECTION_TYPES = __CONNECTION_TYPES__;  // [{{key,label,color}}, ...]
    var CONNECTION_LINES = __CONNECTION_LINES__;  // {{key: [{{path,poiName,infraName,poiType}}, ...]}}
    var CONNECTION_DEAD  = __CONNECTION_DEAD__;   // {{key: [[bool, ...] per stage]}}

    // Backup-lifetime POI layers (optional — empty when not provided). Hourly
    // resolution throughout, unlike everything above (still stage-indexed).
    var BACKUP_POIS        = __BACKUP_POIS__;         // {{poiType: {{shape, points:[{{lat,lon,name}}], pois:[...]}}}}
    var RESOURCE_RING_META = __RESOURCE_RING_META__;  // {{resource: {{label, color}}}}
    var RESTART_THRESHOLD  = __RESTART_THRESHOLD__;   // float, e.g. 0.15

    // RoA time-dependent simulation results (optional — null when not provided)
    var ROA_DATA           = __ROA_DATA__;

    // ── Map setup ────────────────────────────────────────────────────────────
    var map = L.map('map', {{ zoomControl: true }})
                .setView([__CENTER_LAT__, __CENTER_LON__], 12);

    L.tileLayer(
      'https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png',
      {{
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">'
                   + 'OpenStreetMap</a> contributors &copy; '
                   + '<a href="https://carto.com/attributions">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 19
      }}
    ).addTo(map);

    // City boundary (static, decorative)
    L.geoJSON({{ type: 'Feature', geometry: BOUNDARY, properties: {{}} }}, {{
      style: {{
        fillColor:   '#a8c8e0',
        fillOpacity: 0.12,
        color:       '#2255aa',
        weight:      2.2,
        dashArray:   '7 3'
      }},
      interactive: false
    }}).addTo(map);

    // Flood layer — updated every frame
    var floodLayer = L.geoJSON(null, {{
      style: function (feature) {{
        var p = feature.properties.progress || 0;
        return {{
          fillColor:   '#1a60bb',
          fillOpacity: 0.28 + p * 0.37,
          color:       '#0c3d88',
          weight:      0.7,
          opacity:     0.75
        }};
      }},
      interactive: false
    }}).addTo(map);

    // ── Facility / infrastructure markers ──────────────────────────────────────
    var facilityMarkers = {{}};  // key -> array of L.CircleMarker (aligned with FACILITY_POINTS[key])

    FACILITY_TYPES.forEach(function (meta) {{
      var group = L.layerGroup().addTo(map);
      facilityMarkers[meta.key] = (FACILITY_POINTS[meta.key] || []).map(function (pt) {{
        var marker = L.circleMarker([pt.lat, pt.lon], {{
          radius: 7,
          weight: 1.2,
          color: '#ffffff',
          fillColor: meta.color,
          fillOpacity: 0.9
        }});
        marker.bindTooltip(pt.name + ' — ' + meta.label);
        marker.addTo(group);
        return marker;
      }});
    }});

    // ── NCNN connection lines (POI ↔ power/water infrastructure) ──────────────
    var connectionLines = {{}};  // key -> array of L.Polyline (aligned with CONNECTION_LINES[key])

    CONNECTION_TYPES.forEach(function (meta) {{
      var group = L.layerGroup().addTo(map);
      connectionLines[meta.key] = (CONNECTION_LINES[meta.key] || []).map(function (line) {{
        var poly = L.polyline(line.path, {{
          color: meta.color,
          weight: 3,
          opacity: 0.8
        }});
        poly.bindTooltip(line.poiName + ' → ' + line.infraName);
        poly.addTo(group);
        return poly;
      }});
    }});

    // ── Backup-lifetime POI markers (hospitals, fire stations, ...) ───────────
    var STATE_COLOR = {{ Operational: '#2ecc71', Depleting: '#f39c12', Dead: '#8a8a8a', Rebooting: '#8a5cf6' }};
    var STATE_ORDER = ['Operational', 'Depleting', 'Rebooting', 'Dead'];
    var RING_R    = {{ power: 13, water: 18 }};   // power inner, water outer — visual.md §6
    var RING_CIRC = {{ power: 2 * Math.PI * RING_R.power, water: 2 * Math.PI * RING_R.water }};

    function buildBackupIconHtml(uid, shapeKind) {{
      var ringsHtml = '';
      ['power', 'water'].forEach(function (res) {{
        var r = RING_R[res];
        var color = (RESOURCE_RING_META[res] || {{}}).color || '#888888';
        ringsHtml += '<circle cx="22" cy="22" r="' + r + '" fill="none" stroke="#444a58" stroke-width="2.5" opacity="0.35"/>';
        ringsHtml += '<circle id="ring-' + uid + '-' + res + '" cx="22" cy="22" r="' + r
                    + '" fill="none" stroke="' + color + '" stroke-width="2.5" stroke-linecap="round"'
                    + ' transform="rotate(-90 22 22)"/>';
      }});
      var shapeHtml = (shapeKind === 'triangle')
        ? '<polygon id="marker-' + uid + '" points="22,14 15,27 29,27" fill="#2ecc71" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>'
        : '<circle id="marker-' + uid + '" cx="22" cy="22" r="8" fill="#2ecc71" stroke="#fff" stroke-width="1.6"/>';
      return '<svg width="44" height="44" viewBox="0 0 44 44" xmlns="http://www.w3.org/2000/svg">' + ringsHtml + shapeHtml + '</svg>';
    }}

    var backupMarkers = {{}};  // poiType -> [{{marker, els: {{shape, ringPower, ringWater}}}}, ...]
    var selectedBackup = null;  // {{poiType, idx}} or null

    Object.keys(BACKUP_POIS).forEach(function (poiType) {{
      var layer = BACKUP_POIS[poiType];
      var group = L.layerGroup().addTo(map);
      backupMarkers[poiType] = layer.points.map(function (pt, idx) {{
        var uid = poiType + '-' + idx;
        var icon = L.divIcon({{
          className: 'backup-marker-icon',
          html: buildBackupIconHtml(uid, layer.shape),
          iconSize: [44, 44],
          iconAnchor: [22, 22]
        }});
        var marker = L.marker([pt.lat, pt.lon], {{ icon: icon }});
        marker.bindTooltip(pt.name);
        marker.on('click', function (e) {{
          if (e && e.originalEvent) e.originalEvent.stopPropagation();
          selectedBackup = {{ poiType: poiType, idx: idx }};
          updateBackupPanel(currentFrame);
        }});
        marker.addTo(group);

        var root = marker.getElement();
        var els = {{
          shape:     root ? root.querySelector('#marker-' + uid) : null,
          ringPower: root ? root.querySelector('#ring-' + uid + '-power') : null,
          ringWater: root ? root.querySelector('#ring-' + uid + '-water') : null
        }};
        return {{ marker: marker, els: els }};
      }});
    }});

    map.on('click', function () {{
      if (selectedBackup) {{
        selectedBackup = null;
        updateBackupPanel(currentFrame);
      }}
    }});

    function updateBackupMarkers(hour) {{
      Object.keys(BACKUP_POIS).forEach(function (poiType) {{
        var layer = BACKUP_POIS[poiType];
        var markers = backupMarkers[poiType] || [];
        markers.forEach(function (m, idx) {{
          var poi = layer.pois[idx];
          var state = poi.state_by_hour[hour];
          if (m.els.shape) {{
            m.els.shape.setAttribute('fill', STATE_COLOR[state]);
            m.els.shape.classList.toggle('backup-blinking', state === 'Rebooting');
          }}
          var ringEls = {{ power: m.els.ringPower, water: m.els.ringWater }};
          Object.keys(ringEls).forEach(function (res) {{
            var dep = poi.deps[res];
            var ringEl = ringEls[res];
            if (!dep || !ringEl) return;
            var frac = dep.fraction_by_hour[hour];
            var circ = RING_CIRC[res];
            ringEl.setAttribute('stroke-dasharray', circ);
            ringEl.setAttribute('stroke-dashoffset', circ * (1 - frac));
            ringEl.setAttribute('stroke', frac <= 0 ? '#5a5a5a' : ((RESOURCE_RING_META[res] || {{}}).color || '#888888'));
          }});
        }});
      }});
    }}

    function pctStr(x) {{ return Math.round(x * 100) + '%'; }}

    var backupPanelEl = document.getElementById('backup-panel');

    function updateBackupPanel(hour) {{
      if (Object.keys(BACKUP_POIS).length === 0) {{
        backupPanelEl.style.display = 'none';
        return;
      }}
      if (!selectedBackup) {{
        var html = '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">' +
                   '<h4 style="margin:0; font-size:13px; color:#e8ecff;">POI Inspector</h4>' +
                   '<span style="font-size:10.5px; color:#8899cc; background:rgba(255,255,255,0.06); padding:1px 5px; border-radius:3px;">No selection</span>' +
                   '</div>';
        html += '<div class="hint" style="margin-top:0; color:#b0b8dc; line-height:1.45;">' +
                'Click any <b>hospital</b> or <b>fire station</b> on the map to inspect its real-time state, backup power & water buffers, and infrastructure connectivity.' +
                '</div>';
        backupPanelEl.innerHTML = html;
        backupPanelEl.style.display = 'block';
        return;
      }}
      var layer = BACKUP_POIS[selectedBackup.poiType];
      var poi = layer ? layer.pois[selectedBackup.idx] : null;
      var pt  = layer ? layer.points[selectedBackup.idx] : null;
      if (!poi || !pt) {{
        selectedBackup = null;
        updateBackupPanel(hour);
        return;
      }}

      var state = poi.state_by_hour[hour];
      var flooded = poi.flooded_by_hour ? !!poi.flooded_by_hour[hour] : false;
      var html = '<h4>' + pt.name + '</h4>';
      html += '<div class="row"><span>State</span><b style="color:' + STATE_COLOR[state] + '">' + state + '</b></div>';
      html += '<div class="row"><span>Facility itself flooded</span><span>' + (flooded ? 'yes' : 'no') + '</span></div>';

      var restartNotes = [];
      Object.keys(poi.deps).forEach(function (res) {{
        var dep = poi.deps[res];
        var buffer   = dep.buffer_by_hour[hour];
        var capacity = dep.capacity;
        var frac     = capacity ? buffer / capacity : 0;
        var connected = dep.connected_by_hour[hour];
        var label = (RESOURCE_RING_META[res] || {{}}).label || res;

        var dynLabel;
        if (state === 'Dead' || state === 'Rebooting') dynLabel = 'frozen';
        else if (connected) dynLabel = (buffer < capacity ? 'refilling' : 'full');
        else dynLabel = 'decaying';

        html += '<div class="row"><span>' + label + '</span><span>' + (connected ? 'connected' : 'down') + '</span></div>';
        html += '<div class="row"><span>Buffer</span><span>' + Math.round(buffer * 10) / 10 + ' / ' + capacity
              + ' (' + pctStr(frac) + ', ' + dynLabel + ')</span></div>';

        if (!(connected || frac > RESTART_THRESHOLD)) {{
          restartNotes.push(label + ' ' + pctStr(frac) + ' &le; ' + pctStr(RESTART_THRESHOLD) + ', still disconnected');
        }}
      }});

      if (state === 'Dead') {{
        if (flooded) {{
          html += '<div class="hint">Restart blocked &mdash; the facility\\'s own site is still flooded; '
                + 'no backup reserve can restore it while submerged, regardless of buffer levels.</div>';
        }} else if (restartNotes.length) {{
          html += '<div class="hint">Restart blocked &mdash; ' + restartNotes.join('; ') + '.</div>';
        }}
      }}
      if (state === 'Rebooting') {{
        var rt = poi.reboot_timer_by_hour[hour];
        html += '<div class="row"><span>Reboot progress</span><span>' + rt + ' / ' + poi.recharge_delay + ' h</span></div>';
        html += '<div class="hint">~' + (poi.recharge_delay - rt) + ' h until back online.</div>';
      }}
      html += '<div class="hint">Click another facility to switch selection.</div>';
      backupPanelEl.innerHTML = html;
      backupPanelEl.style.display = 'block';
    }}

    function backupStateCountsHtml(poiType) {{
      return '<span class="backup-state-counts" id="backup-counts-' + poiType + '">' +
        STATE_ORDER.map(function (s) {{
          return '<span style="color:' + STATE_COLOR[s] + '">' + s.charAt(0) + ':<b id="backup-count-' + poiType + '-' + s + '">0</b></span>';
        }}).join('') +
      '</span>';
    }}

    function updateBackupCounts(hour) {{
      Object.keys(BACKUP_POIS).forEach(function (poiType) {{
        var counts = {{ Operational: 0, Depleting: 0, Rebooting: 0, Dead: 0 }};
        BACKUP_POIS[poiType].pois.forEach(function (poi) {{ counts[poi.state_by_hour[hour]]++; }});
        STATE_ORDER.forEach(function (s) {{
          var el = document.getElementById('backup-count-' + poiType + '-' + s);
          if (el) el.textContent = counts[s];
        }});
      }});
    }}

    var facilityPanelEl = document.getElementById('facility-panel');
    if (FACILITY_TYPES.length > 0 || CONNECTION_TYPES.length > 0 || Object.keys(BACKUP_POIS).length > 0) {{
      facilityPanelEl.style.display = 'block';
      var panelHtml = FACILITY_TYPES.map(function (meta) {{
        return '<div class="facility-row">' +
                 '<span class="facility-swatch" style="background:' + meta.color + '"></span>' +
                 '<span class="facility-label">' + meta.label + '</span>' +
                 '<span class="facility-count" id="facility-count-' + meta.key + '">–</span>' +
               '</div>';
      }}).join('');
      if (Object.keys(BACKUP_POIS).length > 0) {{
        panelHtml += '<div class="panel-subheader">Backup-lifetime facilities</div>';
        panelHtml += Object.keys(BACKUP_POIS).map(function (poiType) {{
          return '<div class="facility-row">' +
                   '<span class="facility-label">' + poiType.replace('_', ' ') + '</span>' +
                 '</div>' + backupStateCountsHtml(poiType);
        }}).join('');
      }}
      if (CONNECTION_TYPES.length > 0) {{
        panelHtml += '<div class="panel-subheader">Connections</div>';
        panelHtml += CONNECTION_TYPES.map(function (meta) {{
          return '<div class="facility-row">' +
                   '<span class="connection-swatch" style="background:' + meta.color + '"></span>' +
                   '<span class="facility-label">' + meta.label + '</span>' +
                   '<span class="facility-count" id="connection-count-' + meta.key + '">–</span>' +
                 '</div>';
        }}).join('');
      }}
      facilityPanelEl.innerHTML = panelHtml;
    }}

    function updateFacilityMarkers(stageIdx) {{
      FACILITY_TYPES.forEach(function (meta) {{
        var key        = meta.key;
        var floodedArr = (FACILITY_FLOODED[key] || [])[stageIdx] || [];
        var markers    = facilityMarkers[key] || [];
        var points     = FACILITY_POINTS[key] || [];
        var floodedCount = 0;

        markers.forEach(function (marker, i) {{
          var isFlooded = !!floodedArr[i];
          if (isFlooded) floodedCount++;
          marker.setStyle({{
            fillColor:   isFlooded ? '#8a8a8a' : meta.color,
            color:       isFlooded ? '#5a5a5a' : '#ffffff',
            fillOpacity: isFlooded ? 0.35 : 0.9
          }});
          var status = isFlooded ? 'Dead — unavailable' : 'Available';
          marker.setTooltipContent(points[i].name + ' — ' + meta.label + '<br>' + status);
        }});

        var countEl = document.getElementById('facility-count-' + key);
        if (countEl) {{
          countEl.textContent = (markers.length - floodedCount) + ' / ' + markers.length + ' available';
        }}
      }});
    }}

    function updateConnectionLines(stageIdx) {{
      CONNECTION_TYPES.forEach(function (meta) {{
        var key      = meta.key;
        var deadArr  = (CONNECTION_DEAD[key] || [])[stageIdx] || [];
        var polylines = connectionLines[key] || [];
        var deadCount = 0;

        polylines.forEach(function (poly, i) {{
          var isDead = !!deadArr[i];
          if (isDead) deadCount++;
          poly.setStyle({{
            color:   isDead ? '#777777' : meta.color,
            opacity: isDead ? 0.35 : 0.8,
            weight:  isDead ? 2 : 3
          }});
          var line = (CONNECTION_LINES[key] || [])[i];
          var status = isDead ? 'Dead — unavailable' : 'Operational';
          if (line) {{
            poly.setTooltipContent(line.poiName + ' → ' + line.infraName + '<br>' + status);
          }}
        }});

        var countEl = document.getElementById('connection-count-' + key);
        if (countEl) {{
          countEl.textContent = (polylines.length - deadCount) + ' / ' + polylines.length + ' operational';
        }}
      }});
    }}

    // ── RoA Resilience Indicator & Chart ──────────────────────────────────────
    if (ROA_DATA) {{
      var roaPanel = document.getElementById('roa-panel');
      if (roaPanel) roaPanel.style.display = 'block';

      var intBadge = document.getElementById('roa-int-badge');
      if (intBadge && ROA_DATA.roa_int_combined !== undefined) {{
        intBadge.textContent = 'RoA_Int: ' + (ROA_DATA.roa_int_combined * 100).toFixed(1) + '%';
      }}

      var svgLine = document.getElementById('roa-svg-line');
      var svgFill = document.getElementById('roa-svg-fill');
      if (svgLine && svgFill && ROA_DATA.roa_combined) {{
        var pts = [];
        var nH = ROA_DATA.roa_combined.length;
        for (var h = 0; h < nH; h++) {{
          var val = ROA_DATA.roa_combined[h];
          var y = 35 - (val * 32);
          pts.push(h + ',' + y.toFixed(1));
        }}
        var linePath = 'M ' + pts.join(' L ');
        svgLine.setAttribute('d', linePath);
        var fillPath = linePath + ' L ' + (nH - 1) + ',38 L 0,38 Z';
        svgFill.setAttribute('d', fillPath);
      }}
    }}

    function updateRoAPanel(frame) {{
      if (!ROA_DATA) return;
      var val = (ROA_DATA.roa_combined && ROA_DATA.roa_combined[frame] !== undefined)
        ? ROA_DATA.roa_combined[frame]
        : 1.0;
      var curEl = document.getElementById('roa-current-val');
      if (curEl) {{
        curEl.textContent = (val * 100).toFixed(1) + '%';
        if (val >= 0.80) curEl.style.color = '#50e3c2';
        else if (val >= 0.50) curEl.style.color = '#ffaa00';
        else curEl.style.color = '#ff4d4d';
      }}

      if (ROA_DATA.roa_by_type) {{
        if (ROA_DATA.roa_by_type.hospital && document.getElementById('roa-hosp-val')) {{
          var hVal = ROA_DATA.roa_by_type.hospital[frame];
          document.getElementById('roa-hosp-val').textContent = (hVal * 100).toFixed(1) + '%';
        }}
        if (ROA_DATA.roa_by_type.fire_station && document.getElementById('roa-fire-val')) {{
          var fVal = ROA_DATA.roa_by_type.fire_station[frame];
          document.getElementById('roa-fire-val').textContent = (fVal * 100).toFixed(1) + '%';
        }}
      }}

      var playhead = document.getElementById('roa-playhead');
      if (playhead) {{
        playhead.setAttribute('x1', frame);
        playhead.setAttribute('x2', frame);
      }}
    }}

    // ── Phase meta ───────────────────────────────────────────────────────────
    var PHASE_LABELS  = [
      'Pre-event',
      'Rising water',
      'Peak flood \u2014 HQ100',
      'Receding water',
      'Post-event'
    ];
    var PHASE_SEG_IDS = [
      'seg-pre', 'seg-rise', 'seg-peak', 'seg-fall', 'seg-post'
    ];

    function getPhase(hour) {{
      if (hour < 48)  return 0;
      if (hour < 144) return 1;
      if (hour < 192) return 2;
      if (hour < 288) return 3;
      return 4;
    }}

    // ── Animation state ──────────────────────────────────────────────────────
    var currentFrame = 0;
    var playing      = false;
    var animTimer    = null;
    var fps          = 8;

    function setFrame(frame) {{
      frame = Math.max(0, Math.min(335, frame));
      currentFrame = frame;

      var stageIdx = STAGE_FOR_HOUR[frame];
      var geom     = FLOOD_STAGES[stageIdx];
      var progress = stageIdx / (FLOOD_STAGES.length - 1);

      // Update flood polygon
      floodLayer.clearLayers();
      if (geom !== null && geom !== undefined) {{
        floodLayer.addData({{
          type:       'Feature',
          geometry:   geom,
          properties: {{ progress: progress }}
        }});
      }}

      // Update facility / infrastructure availability
      updateFacilityMarkers(stageIdx);

      // Update NCNN connection status (power/water dependency links)
      updateConnectionLines(stageIdx);

      // Update backup-lifetime facilities — hourly resolution, uses frame
      // directly rather than stageIdx (buffer depletion is a per-hour process
      // a 50-stage lookup can't approximate).
      updateBackupMarkers(frame);
      updateBackupCounts(frame);
      updateBackupPanel(frame);
      updateRoAPanel(frame);

      // Update time label
      var day     = Math.floor(frame / 24) + 1;
      var hourNum = frame % 24;
      var phase   = getPhase(frame);
      document.getElementById('time-display').innerHTML =
        'Day ' + day + ' &mdash; ' + String(hourNum).padStart(2, '0') + ':00'
        + ' &nbsp;|&nbsp; ' + PHASE_LABELS[phase];

      // Update slider
      document.getElementById('time-slider').value = frame;

      // Highlight active phase segment
      PHASE_SEG_IDS.forEach(function (id, idx) {{
        document.getElementById(id).classList.toggle('active', idx === phase);
      }});
    }}

    function startLoop() {{
      if (animTimer) clearInterval(animTimer);
      animTimer = setInterval(function () {{
        var next = currentFrame + 1;
        if (next > 335) {{
          pauseAnim();
          return;
        }}
        setFrame(next);
      }}, Math.round(1000 / fps));
    }}

    function pauseAnim() {{
      playing = false;
      if (animTimer) {{ clearInterval(animTimer); animTimer = null; }}
      document.getElementById('btn-play').innerHTML = '&#9654; Play';
    }}

    function playAnim() {{
      playing = true;
      document.getElementById('btn-play').innerHTML = '&#9646;&#9646; Pause';
      startLoop();
    }}

    // ── Controls ─────────────────────────────────────────────────────────────
    document.getElementById('btn-play').addEventListener('click', function () {{
      if (playing) {{ pauseAnim(); }} else {{ playAnim(); }}
    }});

    document.getElementById('btn-prev').addEventListener('click', function () {{
      pauseAnim(); setFrame(currentFrame - 1);
    }});
    document.getElementById('btn-next').addEventListener('click', function () {{
      pauseAnim(); setFrame(currentFrame + 1);
    }});
    document.getElementById('btn-prev-day').addEventListener('click', function () {{
      pauseAnim(); setFrame(currentFrame - 24);
    }});
    document.getElementById('btn-next-day').addEventListener('click', function () {{
      pauseAnim(); setFrame(currentFrame + 24);
    }});

    document.getElementById('time-slider').addEventListener('input', function () {{
      pauseAnim(); setFrame(parseInt(this.value, 10));
    }});

    document.getElementById('speed-select').addEventListener('change', function () {{
      fps = parseInt(this.value, 10);
      if (playing) {{ startLoop(); }}
    }});

    // Keyboard shortcuts
    document.addEventListener('keydown', function (e) {{
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      if (e.key === ' ' || e.key === 'k') {{
        e.preventDefault();
        if (playing) {{ pauseAnim(); }} else {{ playAnim(); }}
      }} else if (e.key === 'ArrowRight' || e.key === 'l') {{
        pauseAnim(); setFrame(currentFrame + 1);
      }} else if (e.key === 'ArrowLeft'  || e.key === 'j') {{
        pauseAnim(); setFrame(currentFrame - 1);
      }} else if (e.key === 'ArrowUp') {{
        pauseAnim(); setFrame(currentFrame + 24);
      }} else if (e.key === 'ArrowDown') {{
        pauseAnim(); setFrame(currentFrame - 24);
      }}
    }});

    // Initialise at frame 0
    setFrame(0);
  </script>
</body>
</html>
""".format(lv=_LEAFLET_VERSION)


def _facility_layers_to_js(
    facility_layers: Optional[Dict[str, Dict]],
) -> tuple[str, str, str]:
    """Serialise facility/infrastructure layer definitions into the three JS
    literals consumed by the animation template.

    Each entry in *facility_layers* has the shape::

        {
            "hospital": {
                "gdf": <GeoDataFrame>,                    # geometry + optional "name" column
                "flooded_by_stage": [[bool, ...], ...],   # from flood_status.compute_flood_status_by_stage
                "label": "Hospitals",
                "color": "#CC2222",
            },
            ...
        }

    Returns
    -------
    tuple of str
        ``(facility_types_js, facility_points_js, facility_flooded_js)`` —
        JSON literals ready for template substitution.
    """
    if not facility_layers:
        return "[]", "{}", "{}"

    types_meta: List[Dict] = []
    points_by_type: Dict[str, List[Dict]] = {}
    flooded_by_type: Dict[str, List[List[bool]]] = {}

    for ftype, layer in facility_layers.items():
        gdf = layer["gdf"]
        label = layer.get("label", ftype)
        color = layer.get("color", "#888888")

        types_meta.append({"key": ftype, "label": label, "color": color})

        points: List[Dict] = []
        for _, row in gdf.iterrows():
            centroid = row.geometry.centroid
            name = row.get("name") if "name" in row.index else None
            name = str(name) if name is not None and pd.notna(name) else label
            points.append({"lat": centroid.y, "lon": centroid.x, "name": name})
        points_by_type[ftype] = points

        flooded_by_type[ftype] = layer.get("flooded_by_stage", [])

    return (
        json.dumps(types_meta, separators=(",", ":")),
        json.dumps(points_by_type, separators=(",", ":")),
        json.dumps(flooded_by_type, separators=(",", ":")),
    )


def _route_geometry_to_latlon(geom) -> List[List[float]]:
    """Flatten a (Multi)LineString NCNN route geometry into a single ordered
    list of ``[lat, lon]`` pairs suitable for a Leaflet polyline.

    Consecutive duplicate points (shared endpoints between the individual
    road-edge segments that make up the route) are collapsed so the
    resulting path draws as one continuous line.

    Individual parts are not guaranteed to be oriented head-to-tail: NCNN
    routes are computed on an undirected graph (``road_network.to_undirected()``),
    and converting a two-way street's directed edges to a single undirected
    edge can silently keep the geometry running in either direction. Each
    part is therefore oriented (reversed if needed) to continue from wherever
    the accumulated path currently ends, instead of trusting storage order.
    """
    if geom is None:
        return []
    if geom.geom_type == "LineString":
        parts = [geom]
    elif geom.geom_type == "MultiLineString":
        parts = list(geom.geoms)
    else:
        return []

    parts_points = [[[lat, lon] for lon, lat in part.coords] for part in parts]
    parts_points = [pts for pts in parts_points if pts]
    if not parts_points:
        return []

    # The first part has no predecessor to orient against, so use the second
    # part (if any) as a directional reference instead of trusting storage
    # order — the first part may itself be stored back-to-front.
    if len(parts_points) > 1:
        first, second = parts_points[0], parts_points[1]
        second_ends = (second[0], second[-1])
        if first[0] in second_ends and first[-1] not in second_ends:
            first.reverse()

    path: List[List[float]] = []
    for part_points in parts_points:
        if path and part_points[-1] == path[-1] and part_points[0] != path[-1]:
            part_points = list(reversed(part_points))
        for point in part_points:
            if path and path[-1] == point:
                continue
            path.append(point)
    return path


def _connection_layers_to_js(
    connection_layers: Optional[Dict[str, Dict]],
) -> tuple[str, str, str]:
    """Serialise POI↔infrastructure NCNN connection layers into the three JS
    literals consumed by the animation template.

    Each entry in *connection_layers* has the shape::

        {
            "power": {
                "label": "Power Connections",
                "color": "#FFD500",
                "poi_connections": {
                    "hospital": {
                        "gdf": ncnn_results["hospital"]["power"],   # NCNN result:
                                                                     # poi_name, infra_name,
                                                                     # geometry (route), ...
                        "dead_by_stage": [[bool, ...], ...],  # aligned with gdf rows,
                                                                # from compute_dependency_status_by_stage
                    },
                    "fire_station": {...},
                },
            },
            "water": {...},
        }

    Connection rows with no route geometry (unreachable POI) are dropped;
    the corresponding per-stage status entries are dropped in lockstep so
    the returned line list and status arrays stay aligned.

    Returns
    -------
    tuple of str
        ``(connection_types_js, connection_lines_js, connection_dead_js)``.
    """
    if not connection_layers:
        return "[]", "{}", "{}"

    types_meta: List[Dict] = []
    lines_by_type: Dict[str, List[Dict]] = {}
    dead_by_type: Dict[str, List[List[bool]]] = {}

    for ctype, layer in connection_layers.items():
        label = layer.get("label", ctype)
        color = layer.get("color", "#888888")
        types_meta.append({"key": ctype, "label": label, "color": color})

        lines: List[Dict] = []
        dead_columns: List[List[bool]] = []  # one list per kept line, values per stage
        n_stages = 0

        for poi_type, poi_layer in layer.get("poi_connections", {}).items():
            gdf = poi_layer["gdf"]
            dead_by_stage = poi_layer.get("dead_by_stage", [])
            n_stages = max(n_stages, len(dead_by_stage))

            for pos, (_, row) in enumerate(gdf.iterrows()):
                path = _route_geometry_to_latlon(row.geometry)
                if not path:
                    continue
                poi_name = row.get("poi_name") or poi_type
                infra_name = row.get("infra_name") or ctype
                lines.append({
                    "path": path,
                    "poiName": str(poi_name),
                    "infraName": str(infra_name),
                    "poiType": poi_type,
                })
                dead_columns.append([bool(stage[pos]) for stage in dead_by_stage])

        lines_by_type[ctype] = lines
        # Transpose per-line/per-stage columns into per-stage/per-line rows,
        # matching the shape used by FACILITY_FLOODED.
        dead_by_type[ctype] = [
            [col[s] for col in dead_columns] for s in range(n_stages)
        ]

    return (
        json.dumps(types_meta, separators=(",", ":")),
        json.dumps(lines_by_type, separators=(",", ":")),
        json.dumps(dead_by_type, separators=(",", ":")),
    )


def _backup_layers_to_js(
    backup_layers: Optional[Dict[str, Dict]],
) -> str:
    """Serialise per-facility backup-lifetime simulation results (see
    ``utils.backup_lifetime.compute_backup_lifetime``) into the JS literal
    consumed by the animation template.

    Each entry in *backup_layers* has the shape::

        {
            "hospital": {
                "gdf": <GeoDataFrame>,              # geometry + optional "name"
                "shape": "circle" | "triangle",
                "cfg": {"recharge_delay": int,
                        "resources": {resource: {"capacity": float, ...}}},
                "backup": [<simulate_backup_lifetime_for_poi() result>, ...],  # aligned with gdf rows
            },
            "fire_station": {...},
        }

    Unlike facility/connection layers (still stage-resolution), this data is
    hourly throughout — buffer depletion is a per-hour process that a 50-stage
    lookup can't approximate.

    Returns
    -------
    str
        JSON literal: ``{poi_type: {"shape": str, "points": [{lat,lon,name}, ...],
        "pois": [{"state_by_hour", "reboot_timer_by_hour", "flooded_by_hour",
        "recharge_delay", "deps": {resource: {"capacity", "buffer_by_hour",
        "fraction_by_hour", "connected_by_hour"}}}, ...]}}``.
    """
    if not backup_layers:
        return "{}"

    out: Dict[str, Dict] = {}
    for poi_type, layer in backup_layers.items():
        gdf = layer["gdf"]
        cfg = layer["cfg"]
        backup_rows = layer["backup"]

        points: List[Dict] = []
        for _, row in gdf.iterrows():
            centroid = row.geometry.centroid
            name = row.get("name") if "name" in row.index else None
            name = str(name) if name is not None and pd.notna(name) else poi_type
            points.append({"lat": centroid.y, "lon": centroid.x, "name": name})

        pois_js: List[Dict] = []
        for sim in backup_rows:
            deps: Dict[str, Dict] = {}
            for resource, resource_cfg in cfg["resources"].items():
                capacity = resource_cfg["capacity"]
                buffer_by_hour = sim["buffer_by_hour"][resource]
                deps[resource] = {
                    "capacity": capacity,
                    "buffer_by_hour": buffer_by_hour,
                    "fraction_by_hour": [
                        (b / capacity if capacity else 0.0) for b in buffer_by_hour
                    ],
                    "connected_by_hour": sim["connected_by_hour"][resource],
                }
            pois_js.append({
                "state_by_hour": sim["state_by_hour"],
                "reboot_timer_by_hour": sim["reboot_timer_by_hour"],
                "flooded_by_hour": sim["flooded_by_hour"],
                "recharge_delay": cfg["recharge_delay"],
                "deps": deps,
            })

        out[poi_type] = {
            "shape": layer.get("shape", "circle"),
            "points": points,
            "pois": pois_js,
        }

    return json.dumps(out, separators=(",", ":"))


def build_flood_animation_html(
    boundary_geom: "BaseGeometry",
    stages: List[Dict],
    output_path: Path,
    center_lat: float = 49.754,
    center_lon: float = 6.649,
    facility_layers: Optional[Dict[str, Dict]] = None,
    connection_layers: Optional[Dict[str, Dict]] = None,
    backup_layers: Optional[Dict[str, Dict]] = None,
    resource_ring_meta: Optional[Dict[str, Dict]] = None,
    restart_threshold: float = 0.15,
    roa_data: Optional[Dict[str, Any]] = None,
) -> Path:
    """Generate a self-contained HTML flood simulation animation file.

    The resulting file embeds a Leaflet.js map with all pre-computed flood
    stage geometries and a JavaScript animation engine — no server or kernel
    interaction is required after generation.

    Animation controls
    ------------------
    * **Space / K** — play / pause
    * **← →** — step one hour
    * **↑ ↓** — step one day
    * **Slider** — jump to any simulation hour
    * **Speed** — 1 / 4 / 8 / 16 / 24 fps

    Parameters
    ----------
    boundary_geom : BaseGeometry
        City boundary polygon in EPSG:4326 (used for the decorative outline).
    stages : list of dict
        Stage list returned by :func:`load_or_compute_flood_stages`.
    output_path : Path
        Destination ``.html`` file path.
    center_lat : float
        Leaflet map initial centre latitude (default: Trier centre).
    center_lon : float
        Leaflet map initial centre longitude (default: Trier centre).
    facility_layers : dict, optional
        ``{facility_type: {"gdf": GeoDataFrame, "flooded_by_stage": [...],
        "label": str, "color": str}}``. When provided, each facility type is
        drawn as a marker layer whose colour dims to grey for stages where
        that facility is flooded (per ``flooded_by_stage``, typically from
        :func:`css_geodata_service.robustness_of_accessibility.utils.flood_status.compute_flood_status_by_stage`).
        Omit (default ``None``) to reproduce the plain flood-only animation.
    connection_layers : dict, optional
        ``{infra_type: {"label": str, "color": str, "poi_connections": {poi_type:
        {"gdf": <NCNN route GeoDataFrame>, "dead_by_stage": [...]}}}}``. When
        provided, draws each NCNN POI→infrastructure route as a coloured
        polyline that dims to grey for stages where the connection is dead
        (per :func:`css_geodata_service.robustness_of_accessibility.utils.flood_status.compute_dependency_status_by_stage`).
        Omit (default ``None``) to draw no connection lines.
    backup_layers : dict, optional
        ``{poi_type: {"gdf": GeoDataFrame, "shape": "circle" | "triangle",
        "cfg": {...}, "backup": [...]}}`` — see
        :func:`_backup_layers_to_js` for the exact shape, and
        :func:`css_geodata_service.robustness_of_accessibility.utils.backup_lifetime.compute_backup_lifetime`
        for how to produce the ``"backup"`` entries. When provided, each POI
        type is rendered with the full Logic.md/visual.md treatment — 4-state
        marker color, shape-by-type, dual reserve rings, blink-only
        `Rebooting`, and a click-to-inspect panel — **instead of** the plain
        ``facility_layers`` marker for that same type. Resolution is hourly
        throughout, not stage-based (buffer depletion needs it). Omit
        (default ``None``) to reproduce the animation without backup-lifetime
        facilities.
    resource_ring_meta : dict, optional
        ``{resource: {"label": str, "color": str}}`` for the two reserve
        rings, e.g. ``{"power": {"label": "Power", "color": "#FF8C00"},
        "water": {"label": "Water", "color": "#00AEEF"}}``. Defaults to
        exactly that pairing if omitted (matches the demo prototype and
        visual.md §5).
    restart_threshold : float
        Hysteresis guard fraction shown in the click panel's Restart
        Viability readout (Logic.md §4/§5) — purely a display value here,
        the actual gate was already applied when *backup_layers* was
        computed.
    roa_data : dict, optional
        ``{hours: [...], roa_combined: [...], roa_by_type: {...}, roa_int_combined: float}``
        from :func:`load_or_compute_dynamic_roa`. When provided, displays a synchronized
        live RoA status HUD, service breakdown, total RoA_Int resilience score, and
        an animated timeline sparkline chart. Omit (default ``None``) to omit the panel.

    Returns
    -------
    Path
        Absolute path to the written HTML file.
    """
    # ------------------------------------------------------------------
    # 1. Build the stage-for-hour lookup (336 entries)
    # ------------------------------------------------------------------
    stage_for_hour = build_stage_for_hour(stages)

    # ------------------------------------------------------------------
    # 2. Simplify boundary geometry for compact embedding
    # ------------------------------------------------------------------
    boundary_simplified = boundary_geom.simplify(0.001, preserve_topology=True)
    boundary_js = json.dumps(
        boundary_simplified.__geo_interface__, separators=(",", ":")
    )

    # ------------------------------------------------------------------
    # 3. Serialise flood stages as a JS array of geometry dicts
    # ------------------------------------------------------------------
    flood_stages_js = json.dumps(
        [s["geojson"] for s in stages], separators=(",", ":")
    )
    stage_hours_js = json.dumps(stage_for_hour)

    # ------------------------------------------------------------------
    # 4. Serialise facility/infrastructure layers (optional)
    # ------------------------------------------------------------------
    facility_types_js, facility_points_js, facility_flooded_js = _facility_layers_to_js(
        facility_layers
    )

    # ------------------------------------------------------------------
    # 4b. Serialise NCNN POI↔infrastructure connection layers (optional)
    # ------------------------------------------------------------------
    connection_types_js, connection_lines_js, connection_dead_js = _connection_layers_to_js(
        connection_layers
    )

    # ------------------------------------------------------------------
    # 4c. Serialise backup-lifetime POI layers (optional)
    # ------------------------------------------------------------------
    backup_pois_js = _backup_layers_to_js(backup_layers)
    resource_ring_meta = resource_ring_meta or {
        "power": {"label": "Power", "color": "#FF8C00"},
        "water": {"label": "Water", "color": "#00AEEF"},
    }
    resource_ring_meta_js = json.dumps(resource_ring_meta, separators=(",", ":"))
    restart_threshold_js = json.dumps(restart_threshold)
    roa_data_js = (
        json.dumps(roa_data, separators=(",", ":")) if roa_data is not None else "null"
    )

    # ------------------------------------------------------------------
    # 5. Inject into template and write
    # ------------------------------------------------------------------
    html = _ANIMATION_HTML_TEMPLATE
    html = html.replace("__BOUNDARY__",          boundary_js)
    html = html.replace("__FLOOD_STAGES__",      flood_stages_js)
    html = html.replace("__STAGE_HOURS__",       stage_hours_js)
    html = html.replace("__CENTER_LAT__",        str(round(center_lat, 4)))
    html = html.replace("__CENTER_LON__",        str(round(center_lon, 4)))
    html = html.replace("__BACKUP_POIS__",       backup_pois_js)
    html = html.replace("__RESOURCE_RING_META__", resource_ring_meta_js)
    html = html.replace("__RESTART_THRESHOLD__", restart_threshold_js)
    html = html.replace("__ROA_DATA__",          roa_data_js)
    html = html.replace("__FACILITY_TYPES__",    facility_types_js)
    html = html.replace("__FACILITY_POINTS__",   facility_points_js)
    html = html.replace("__FACILITY_FLOODED__",  facility_flooded_js)
    html = html.replace("__CONNECTION_TYPES__",  connection_types_js)
    html = html.replace("__CONNECTION_LINES__",  connection_lines_js)
    html = html.replace("__CONNECTION_DEAD__",   connection_dead_js)

    output_path.write_text(html, encoding="utf-8")
    size_kb = output_path.stat().st_size / 1024
    logger.info(
        "Flood simulation HTML saved: %s  (%.0f KB)",
        output_path, size_kb,
    )
    return output_path.resolve()
