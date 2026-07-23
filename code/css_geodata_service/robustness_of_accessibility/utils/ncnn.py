"""
Network-Constrained Nearest-Neighbor (NCNN) routes: POIs → infrastructure.

Power and water infrastructure runs underground along street corridors.
The NCNN algorithm finds, for each POI (hospital, fire station …), the
nearest infrastructure node (power substation, water works …) using
road-network distance instead of straight-line distance, reflecting the
physical reality that utility networks follow street layouts.

Algorithm (reuses the existing multi-source Dijkstra already used in
calculate_routes_multi_dijkstra):
  1. Snap each infrastructure centroid to the nearest road-network node.
  2. Run nx.multi_source_dijkstra from all infrastructure nodes as sources.
  3. For each POI road-node, read off distance + path to the nearest source.
  4. Reconstruct MultiLineString route geometry from path node sequence.
  5. Persist results as GeoJSON in data/processed/ncnn/ for downstream use.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd
from shapely import MultiLineString, reverse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _snap_to_network(
    gdf: gpd.GeoDataFrame,
    graph: nx.MultiGraph | nx.MultiDiGraph,
    street_nodes: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Snap each feature's centroid to the nearest road-network node.

    Adds two columns:
      nearest_node_id    – OSM node ID of the closest road node
      nearest_node_point – geometry (Point) of that node
    """
    gdf = gdf.copy()
    centroids = gdf.geometry.centroid
    gdf["nearest_node_id"] = [
        ox.distance.nearest_nodes(graph, X=pt.x, Y=pt.y) for pt in centroids
    ]
    gdf["nearest_node_point"] = gdf["nearest_node_id"].apply(
        lambda nid: street_nodes.at[nid, "geometry"]
    )
    return gdf


def _build_route_geometry(
    path: List[int],
    edges: gpd.GeoDataFrame,
) -> Optional[MultiLineString]:
    """Reconstruct a MultiLineString from a list of node IDs (same pattern as
    calculate_routes_multi_dijkstra in utils.py)."""
    if len(path) < 2:
        return None
    route_edges = [(path[i], path[i + 1], 0) for i in range(len(path) - 1)]
    geoms = []
    for edge in route_edges:
        try:
            geom = edges.at[edge, "geometry"]
        except KeyError:
            # if (u, v, 0) not found try reversed edge (v, u, 0)
            reversed_edge = (edge[1], edge[0], edge[2])
            geom = reverse(edges.at[reversed_edge, "geometry"])
        geoms.append(geom)
    return MultiLineString(geoms)


def _safe_str(row: pd.Series, col: str) -> Optional[str]:
    """Return a string value from a series row, or None for missing/NaN."""
    val = row.get(col) if col in row.index else None
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    return str(val)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_ncnn_routes(
    poi_gdf: gpd.GeoDataFrame,
    poi_type: str,
    infrastructure_gdf: gpd.GeoDataFrame,
    infra_type: str,
    street_network: nx.MultiGraph | nx.MultiDiGraph,
) -> gpd.GeoDataFrame:
    """
    For every POI find the nearest infrastructure node via the road network
    (Network-Constrained Nearest-Neighbor, NCNN).

    Uses ``nx.multi_source_dijkstra`` with all infrastructure road-nodes as
    simultaneous sources — the same algorithm used in
    ``calculate_routes_multi_dijkstra``.  Running once per infrastructure type
    is far cheaper than running one Dijkstra per (POI, infra) pair.

    Parameters
    ----------
    poi_gdf : GeoDataFrame
        POI features (hospitals, fire stations …).  Must have a ``geometry``
        column; a ``name`` column is used for labels if present.
    poi_type : str
        Human-readable label, e.g. ``"hospital"``.
    infrastructure_gdf : GeoDataFrame
        Infrastructure features (substations, water works …).  Must have a
        ``geometry`` column; ``name`` is used for labels if present.
    infra_type : str
        Human-readable label, e.g. ``"power_substation"``.
    street_network : MultiGraph | MultiDiGraph
        OSMnx road-network graph.  Pass an undirected graph
        (``.to_undirected()``) to avoid one-way restrictions blocking
        underground-infrastructure paths.

    Returns
    -------
    GeoDataFrame  (CRS EPSG:4326)
        One row per POI.  Columns:

        * ``poi_node_id``    – road-node ID of the POI
        * ``poi_name``       – name string or None
        * ``poi_type``       – ``poi_type`` argument
        * ``poi_position``   – WKT string of the snapped road node
        * ``infra_node_id``  – road-node ID of the nearest infrastructure node
        * ``infra_name``     – name string or None
        * ``infra_type``     – ``infra_type`` argument
        * ``infra_position`` – WKT string of the snapped infrastructure node
        * ``route_length_m`` – network distance in metres (inf if unreachable)
        * ``geometry``       – MultiLineString of the route (None if unreachable)
    """
    logger.info(
        "NCNN: %d %s POI(s) → %d %s infrastructure node(s)",
        len(poi_gdf), poi_type, len(infrastructure_gdf), infra_type,
    )

    # -- obtain nodes and edges once ------------------------------------------
    street_nodes, edges = ox.graph_to_gdfs(street_network)

    # -- snap features to the road network ------------------------------------
    infra_snapped = _snap_to_network(infrastructure_gdf, street_network, street_nodes)
    poi_snapped = _snap_to_network(poi_gdf, street_network, street_nodes)

    # build lookup: road-node-id → infrastructure row
    # (last row wins when multiple infra features snap to the same node,
    # which is fine since they share the same road-node location)
    infra_node_lookup: Dict[int, pd.Series] = {
        int(row["nearest_node_id"]): row
        for _, row in infra_snapped.iterrows()
    }
    infra_sources = set(infra_node_lookup.keys())
    logger.debug("Infrastructure: %d unique road-source nodes", len(infra_sources))

    # -- Multi-source Dijkstra: infrastructure nodes → whole network ----------
    # distance: {node_id: dist_to_nearest_source}
    # path:     {node_id: [source, ..., node_id]}
    distance, path = nx.multi_source_dijkstra(
        street_network,
        sources=infra_sources,
        weight="length",
    )

    # -- build one result record per POI --------------------------------------
    records = []
    for _, poi_row in poi_snapped.iterrows():
        poi_nid = int(poi_row["nearest_node_id"])
        poi_name = _safe_str(poi_row, "name")
        poi_path = path.get(poi_nid)

        if poi_path is None:
            logger.warning(
                "NCNN: no path from any %s node to POI %s (road-node %d)",
                infra_type, poi_name or "?", poi_nid,
            )
            records.append(dict(
                poi_node_id=poi_nid,
                poi_name=poi_name,
                poi_type=poi_type,
                poi_position=poi_row["nearest_node_point"].wkt,
                infra_node_id=None,
                infra_name=None,
                infra_type=infra_type,
                infra_position=None,
                route_length_m=float("inf"),
                geometry=None,
            ))
            continue

        # poi_path[0] is the nearest infrastructure source node
        nearest_infra_nid = int(poi_path[0])
        infra_row = infra_node_lookup.get(nearest_infra_nid)
        infra_name = _safe_str(infra_row, "name") if infra_row is not None else None
        infra_pos_wkt = (
            infra_row["nearest_node_point"].wkt if infra_row is not None else None
        )

        route_length = float(distance.get(poi_nid, float("inf")))
        route_geom = _build_route_geometry(poi_path, edges)

        records.append(dict(
            poi_node_id=poi_nid,
            poi_name=poi_name,
            poi_type=poi_type,
            poi_position=poi_row["nearest_node_point"].wkt,
            infra_node_id=nearest_infra_nid,
            infra_name=infra_name,
            infra_type=infra_type,
            infra_position=infra_pos_wkt,
            route_length_m=route_length,
            geometry=route_geom,
        ))

    result_gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")

    finite_lengths = result_gdf["route_length_m"].replace(float("inf"), float("nan"))
    logger.info(
        "NCNN done: %d routes (%s → %s), mean %.0f m, max %.0f m",
        len(result_gdf), poi_type, infra_type,
        finite_lengths.mean() if not finite_lengths.isna().all() else 0,
        finite_lengths.max() if not finite_lengths.isna().all() else 0,
    )
    return result_gdf


def load_or_calculate_ncnn_routes(
    cache_dir: Path,
    poi_gdfs: Dict[str, gpd.GeoDataFrame],
    infrastructure_gdfs: Dict[str, gpd.GeoDataFrame],
    street_network: nx.MultiGraph | nx.MultiDiGraph,
    place_name: str = "Trier, Germany",
    force_recalculate: bool = False,
) -> Dict[str, Dict[str, gpd.GeoDataFrame]]:
    """
    Cached wrapper around :func:`calculate_ncnn_routes`.

    GeoJSON result files are stored under ``cache_dir / "ncnn" /``.
    File name pattern::

        ncnn_{poi_type}_to_{infra_type}_{safe_place_name}.geojson

    Parameters
    ----------
    cache_dir : Path
        Processed-data directory (``data/processed/``).
    poi_gdfs : dict
        ``{poi_type: GeoDataFrame}`` e.g.
        ``{"hospital": ..., "fire_station": ...}``.
    infrastructure_gdfs : dict
        ``{infra_type: GeoDataFrame}`` e.g.
        ``{"power": ..., "water": ...}``.
    street_network : graph
        OSMnx road network (directed or undirected).
    place_name : str
        Embedded in cache-file names to avoid collisions across cities.
    force_recalculate : bool
        Ignore existing cache files and always recompute.

    Returns
    -------
    dict
        ``{poi_type: {infra_type: GeoDataFrame}}``
    """
    ncnn_cache_dir = cache_dir / "ncnn"
    ncnn_cache_dir.mkdir(parents=True, exist_ok=True)

    safe_place = place_name.replace(", ", "_").replace(" ", "_")
    results: Dict[str, Dict[str, gpd.GeoDataFrame]] = {}

    for poi_type, poi_gdf in poi_gdfs.items():
        results[poi_type] = {}
        for infra_type, infra_gdf in infrastructure_gdfs.items():
            cache_file = (
                ncnn_cache_dir
                / f"ncnn_{poi_type}_to_{infra_type}_{safe_place}.geojson"
            )

            if cache_file.exists() and not force_recalculate:
                logger.info("NCNN: loading cached results from %s", cache_file)
                results[poi_type][infra_type] = gpd.read_file(cache_file)
            else:
                gdf = calculate_ncnn_routes(
                    poi_gdf=poi_gdf,
                    poi_type=poi_type,
                    infrastructure_gdf=infra_gdf,
                    infra_type=infra_type,
                    street_network=street_network,
                )
                gdf.to_file(cache_file, driver="GeoJSON")
                logger.info("NCNN: results cached to %s", cache_file)
                results[poi_type][infra_type] = gdf

    return results
