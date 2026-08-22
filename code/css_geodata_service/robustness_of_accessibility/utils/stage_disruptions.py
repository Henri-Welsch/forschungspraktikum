"""
Stage-based road network disruption computation and disk persistence.

Performs geometric intersection between the road network graph and discrete
flood stage polygons (pre-slicing), and persists the resulting flooded edge sets
to disk cache. During dynamic simulation, downstream routing engines can retrieve
the pre-sliced graph for any flood stage in O(1) time without performing heavy
geometry clipping operations.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import geopandas as gpd
import networkx as nx
import osmnx as ox
from shapely.geometry import shape

logger = logging.getLogger(__name__)


def compute_stage_disruptions(
    street_network: nx.MultiGraph | nx.MultiDiGraph,
    stages: List[Dict],
    keep_bridges: bool = True,
) -> Dict[int, List[Tuple[int, int, int]]]:
    """Compute flooded edge IDs for each discrete flood stage.

    Parameters
    ----------
    street_network : nx.MultiGraph or nx.MultiDiGraph
        Undisrupted road network.
    stages : list of dict
        Stage list as returned by :func:`load_or_compute_flood_stages`. Each
        entry contains ``stage_index``, ``progress``, and ``geojson``.
    keep_bridges : bool, optional
        If True (default), edges with a non-null ``bridge`` attribute are
        assumed elevated and not marked as flooded.

    Returns
    -------
    dict
        ``{stage_idx: [(u, v, key), ...]}`` mapping stage indices to lists of
        flooded edge tuples.
    """
    edges = ox.graph_to_gdfs(street_network, nodes=False, edges=True)
    disruptions: Dict[int, List[Tuple[int, int, int]]] = {}

    for stage in stages:
        idx = stage.get("stage_index", 0)
        geojson_geom = stage.get("geojson")
        flood_geom = shape(geojson_geom) if geojson_geom is not None else None

        if flood_geom is None or flood_geom.is_empty or len(edges) == 0:
            disruptions[idx] = []
            continue

        mask = edges.intersects(flood_geom)
        if keep_bridges and "bridge" in edges.columns:
            mask = mask & edges["bridge"].isna()

        flooded_indices = [tuple(x) for x in edges[mask].index]
        disruptions[idx] = flooded_indices

    logger.info(
        "Computed stage disruptions for %d stages (peak stage has %d flooded edges)",
        len(stages),
        max(len(e) for e in disruptions.values()) if disruptions else 0,
    )
    return disruptions


def load_or_compute_stage_disruptions(
    cache_dir: Path | str,
    street_network: nx.MultiGraph | nx.MultiDiGraph,
    stages: List[Dict],
    place_name: str = "Trier, Germany",
    keep_bridges: bool = True,
    force_recompute: bool = False,
) -> Dict[int, List[Tuple[int, int, int]]]:
    """Load stage-based road network disruptions from cache, or compute and
    persist to disk.

    Parameters
    ----------
    cache_dir : Path or str
        Base cache directory (e.g. ``data/processed``).
    street_network : nx.MultiGraph or nx.MultiDiGraph
        Road network graph.
    stages : list of dict
        Flood stage geometries.
    place_name : str
        Region identifier for cache filename.
    keep_bridges : bool
        Whether to preserve bridges.
    force_recompute : bool
        If True, ignore cache and recalculate.

    Returns
    -------
    dict
        ``{stage_idx: [(u, v, key), ...]}``
    """
    safe_place = place_name.replace(" ", "_").replace(",", "")
    n_stages = len(stages)
    cache_file = (
        Path(cache_dir)
        / "network"
        / f"stage_disruptions_{safe_place}_{n_stages}.json"
    )

    if cache_file.exists() and not force_recompute:
        logger.info("Loading stage disruptions from cache %s", cache_file)
        with open(cache_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # Convert keys to int and edge lists back to tuples
        disruptions = {
            int(k): [tuple(edge) for edge in v]
            for k, v in raw.get("stage_disruptions", {}).items()
        }
        return disruptions

    disruptions = compute_stage_disruptions(
        street_network=street_network,
        stages=stages,
        keep_bridges=keep_bridges,
    )

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "place_name": place_name,
        "n_stages": n_stages,
        "keep_bridges": keep_bridges,
        "stage_disruptions": {
            str(k): [list(edge) for edge in v] for k, v in disruptions.items()
        },
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    logger.info("Stage disruptions saved to cache %s", cache_file)
    return disruptions


def build_stage_graphs(
    base_network: nx.MultiGraph | nx.MultiDiGraph,
    stage_disruptions: Dict[int, List[Tuple[int, int, int]]],
) -> Dict[int, nx.MultiGraph | nx.MultiDiGraph]:
    """Pre-build networkx graph copies for all stages by removing flooded edges.

    Parameters
    ----------
    base_network : nx.MultiGraph or nx.MultiDiGraph
        Undisrupted base street network (typically undirected).
    stage_disruptions : dict
        ``{stage_idx: [(u, v, key), ...]}`` from :func:`load_or_compute_stage_disruptions`.

    Returns
    -------
    dict
        ``{stage_idx: nx.Graph}`` dictionary of passable road network graphs.
    """
    stage_graphs: Dict[int, nx.MultiGraph | nx.MultiDiGraph] = {}
    for stage_idx, flooded_edges in stage_disruptions.items():
        G_k = base_network.copy()
        if flooded_edges:
            G_k.remove_edges_from(flooded_edges)
        stage_graphs[stage_idx] = G_k
    return stage_graphs
