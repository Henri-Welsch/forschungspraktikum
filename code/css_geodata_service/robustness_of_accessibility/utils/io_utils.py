import os

import networkx as nx
import osmnx as ox
import geopandas as gpd
import shapely
from shapely.ops import unary_union


def load_flooded_area_as_multipolygon(file_path: str) -> shapely.geometry.MultiPolygon:
    flooded_area = gpd.read_file(file_path)
    flooded_area_geoms = flooded_area.geometry
    flooded_area_multipolygon_df = gpd.GeoSeries(unary_union(flooded_area_geoms))
    flooded_area_multipolygon = flooded_area_multipolygon_df[0]
    return flooded_area_multipolygon


def load_drive_graphs_from_file(source_dir: str, file_slug: str, undirected_graph: bool):
    file_path_all_private = f"{source_dir}/{file_slug}_all_private.graphml"
    file_path_drive = f"{source_dir}/{file_slug}_drive.graphml"

    drive_graph_drive = ox.load_graphml(file_path_drive)

    drive_graph_all_private = ox.load_graphml(file_path_all_private)

    if undirected_graph:
        drive_graph_all_private = drive_graph_all_private.to_undirected()
        drive_graph_drive = drive_graph_drive.to_undirected()

    return drive_graph_drive, drive_graph_all_private


def read_graph_from_disk(dir_path: str, file_name: str, undirected_graph: bool) -> nx.MultiGraph | nx.MultiDiGraph:
    """
    :param undirected_graph:
    :param dir_path: directory where the graph is stored
    :param file_name: name of the file
    :return: nx.Graph | nx.DiGraph
    """
    if ".graphml" not in file_name:
        file_name += ".graphml"
    graph_path = os.path.join(dir_path, file_name)
    graph = ox.load_graphml(filepath=graph_path)
    if undirected_graph:
        graph = graph.to_undirected()
    return graph


def write_graph_to_disk(graph: nx.MultiGraph | nx.MultiDiGraph, dir_path: str, file_name: str):
    """
    :param graph:
    :param dir_path:
    :param file_name:
    :return:
    """
    if ".graphml" not in file_name:
        file_name += ".graphml"
    ox.save_graphml(graph, filepath=os.path.join(dir_path, file_name))
