import networkx as nx
from shapely import Point, MultiPolygon
import logging
import time
import osmnx as ox
from css_geodata_service.robustness_of_accessibility.utils.utils import (
    unwrap_nested_lists,
)

logger = logging.getLogger(__name__)


def construct_disrupted_graph(
    flooded_area: MultiPolygon,
    street_network: nx.MultiDiGraph,
    keep_bridges: bool = True,
    filter_nodes: bool = False,
) -> nx.MultiDiGraph:
    """
    Constructs a disrupted graph by selecting edges (and nodes if filter_nodes=True) that do not intersect with a given
    Multipolygon. Selected edges (and nodes) are added to a new graph which then gets returned by the function.
    Parameters
    ----------
    flooded_area: describes a flooded area
    street_network: street network consisting of nodes and edges in an undisrupted state
    keep_bridges: indicates whether bridges should be kept even though they are intersecting a flooded area
    filter_nodes: indicates whether nodes within the flooded area should be added to the disrupted graph

    Returns
    -------
    Disrupted graph based on the flooded area and the initial street network
    """

    logger.info("Constructing disrupted graph - this may take a while")
    disrupted_graph = nx.MultiDiGraph()
    disrupted_graph.graph["crs"] = street_network.graph.get("crs", None)
    # iterate through nodes and add nodes outside the flooded area to the subgraph
    for node, data in street_network.nodes(data=True):
        if filter_nodes:
            if not flooded_area.intersects(Point(data["x"], data["y"])):
                disrupted_graph.add_node(node, **data)
        else:
            disrupted_graph.add_node(node, **data)

    # iterate through edges and add edges outside the flooded area to the subgraph
    for source, target, data in street_network.edges(data=True):
        edge_geometry = data.get("geometry", None)
        # adds edge when not flooded, or when edge is bridge and keep_bridges=True
        if (edge_geometry and not flooded_area.intersects(edge_geometry)) or (keep_bridges and data.get("bridge")):
            disrupted_graph.add_edge(source, target, **data)
    return disrupted_graph


def get_graph_affected_by_polygon_from_gdfs(
    nodes,
    edges,
    multipolygon: MultiPolygon,
    exclude_bridges: bool,
    undirected_graph: bool,
    filter_nodes=False,
):
    start = time.time()
    logger.info("Calculate affected and unaffected network")
    # find which edges are within (intersect) the polygon
    mask_affected_edges = edges.intersects(multipolygon)

    # filter those edges
    affected_edges = edges[mask_affected_edges]

    if exclude_bridges:
        # Identify edges that are bridges to exclude them from the affected area
        affected_bridges = affected_edges[affected_edges["bridge"].notnull()]
        # get the ids of the edges that are bridges
        bridge_ids = set(affected_bridges.index.values)
        # identify affected edges that are affected but are not bridges
        mask_affected_edges_without_bridges = ~affected_edges.index.isin(bridge_ids)
        # update the affected edges to exclude bridges
        affected_edges = affected_edges.loc[mask_affected_edges_without_bridges]

    else:
        affected_bridges = None

    affected_edged_ids = set(affected_edges.index.values)
    un_affected_edges = edges.loc[~edges.index.isin(affected_edged_ids)]

    if not filter_nodes:
        # affected_network = ox.graph_from_gdfs(nodes, affected_edges)
        un_affected_network = ox.graph_from_gdfs(nodes, un_affected_edges)
        affected_nodes = None
        common_indices = None
    else:
        # find which nodes are within (intersect) the polygon
        mask_affected_nodes = nodes.intersects(multipolygon)

        # filter those nodes
        affected_nodes = nodes[mask_affected_nodes]

        # filters all ids of nodes that are connected to edges that are affected
        u_ids = set(affected_edges.index.get_level_values("u").unique().to_list())
        v_ids = set(affected_edges.index.get_level_values("v").unique().to_list())
        # osm_ids = affected_edges["osmid"].values.tolist()
        osm_ids_from_nodes_connected_to_affected_edges = [u_ids, v_ids]
        osm_ids_from_nodes_connected_to_affected_edges = unwrap_nested_lists(
            osm_ids_from_nodes_connected_to_affected_edges
        )
        all_node_ids = nodes.index.to_list()
        osm_ids_from_nodes_connected_to_affected_edges = set(all_node_ids).intersection(
            set(osm_ids_from_nodes_connected_to_affected_edges)
        )

        affected_nodes_from_affected_edges = nodes[nodes.index.isin(osm_ids_from_nodes_connected_to_affected_edges)]

        # calculate the intersection i.e. the affected nodes (that intersect the polygon) and those that are
        # also connected to an affected edge?
        common_indices = (affected_nodes_from_affected_edges.index.intersection(affected_nodes.index)).to_list()

        # Subset both dataframes using the common indices
        # affected_nodes = nodes.loc[common_indices]

        # Invert the filter
        inverted_indices = ~nodes.index.isin(common_indices)
        unaffected_nodes = nodes.loc[inverted_indices]

        # create network from gdfs
        # uses all nodes now to avoid any problems with graph / route calculation
        # affected_network = ox.graph_from_gdfs(affected_nodes, affected_edges)
        un_affected_network = ox.graph_from_gdfs(unaffected_nodes, un_affected_edges)

    if undirected_graph:
        # affected_network = affected_network.to_undirected()
        un_affected_network = un_affected_network.to_undirected()

    end = time.time()
    logger.info(f"Construct disrupted graph elapsed time: {end - start}s")
    return un_affected_network
