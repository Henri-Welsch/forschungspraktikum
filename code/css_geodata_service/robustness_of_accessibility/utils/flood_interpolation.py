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
from pathlib import Path
from typing import Dict, List, Optional

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
    features = [
        {
            "type": "Feature",
            "properties": {
                "stage_index": s["stage_index"],
                "progress":    s["progress"],
            },
            "geometry": s["geojson"],   # GeoJSON geometry dict or null
        }
        for s in stages
    ]
    fc = {"type": "FeatureCollection", "features": features}
    with cache_file.open("w", encoding="utf-8") as fh:
        json.dump(fc, fh, separators=(",", ":"))


def _load_stages_from_cache(cache_file: Path) -> List[Dict]:
    """Load stages from a GeoJSON FeatureCollection cache file."""
    with cache_file.open("r", encoding="utf-8") as fh:
        fc = json.load(fh)

    return [
        {
            "stage_index": int(feat["properties"]["stage_index"]),
            "progress":    float(feat["properties"]["progress"]),
            "geojson":     feat.get("geometry"),    # None for no-flood stages
        }
        for feat in fc["features"]
    ]


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

    /* Info badge */
    #info-badge {{
      position: fixed; top: 10px; left: 10px;
      background: rgba(8,8,22,0.88); color: #c8d0f0;
      padding: 8px 14px; border-radius: 6px; font-size: 13px;
      z-index: 1000; line-height: 1.7; pointer-events: none;
    }}

    /* ── Facility / infrastructure panel ─────────────────────── */
    #facility-panel {{
      position: fixed; top: 10px; right: 10px;
      background: rgba(8,8,22,0.90); color: #d8ddf0;
      padding: 10px 14px; border-radius: 6px; font-size: 12.5px;
      z-index: 1000; line-height: 1.6; min-width: 190px;
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
  </style>
</head>
<body>
  <div id="map"></div>

  <div id="info-badge">
    <strong>Trier &mdash; Flood Simulation</strong><br>
    HQ100 &bull; 14-day event &bull; hourly steps
  </div>

  <div id="facility-panel"></div>

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

    var facilityPanelEl = document.getElementById('facility-panel');
    if (FACILITY_TYPES.length > 0 || CONNECTION_TYPES.length > 0) {{
      facilityPanelEl.style.display = 'block';
      var panelHtml = FACILITY_TYPES.map(function (meta) {{
        return '<div class="facility-row">' +
                 '<span class="facility-swatch" style="background:' + meta.color + '"></span>' +
                 '<span class="facility-label">' + meta.label + '</span>' +
                 '<span class="facility-count" id="facility-count-' + meta.key + '">–</span>' +
               '</div>';
      }}).join('');
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


def build_flood_animation_html(
    boundary_geom: "BaseGeometry",
    stages: List[Dict],
    output_path: Path,
    center_lat: float = 49.754,
    center_lon: float = 6.649,
    facility_layers: Optional[Dict[str, Dict]] = None,
    connection_layers: Optional[Dict[str, Dict]] = None,
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

    Returns
    -------
    Path
        Absolute path to the written HTML file.
    """
    # ------------------------------------------------------------------
    # 1. Build the stage-for-hour lookup (336 entries)
    # ------------------------------------------------------------------
    stage_for_hour = [
        get_stage_for_progress(
            stages, compute_hourly_flood_progress(h)
        )["stage_index"]
        for h in range(SIMULATION_HOURS)
    ]

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
    # 5. Inject into template and write
    # ------------------------------------------------------------------
    html = _ANIMATION_HTML_TEMPLATE
    html = html.replace("__BOUNDARY__",          boundary_js)
    html = html.replace("__FLOOD_STAGES__",      flood_stages_js)
    html = html.replace("__STAGE_HOURS__",       stage_hours_js)
    html = html.replace("__CENTER_LAT__",        str(round(center_lat, 4)))
    html = html.replace("__CENTER_LON__",        str(round(center_lon, 4)))
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
