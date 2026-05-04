from colorsys import hsv_to_rgb
from typing import Any, List, Dict

from networkx.algorithms.shortest_paths.astar import astar_path
from networkx.algorithms.shortest_paths.generic import shortest_path
import numpy as np
from networkx.exception import NetworkXNoPath
from pandas import DataFrame
from shapely import MultiLineString, reverse
from shapely.geometry.point import Point

from css_geodata_service.robustness_of_accessibility.utils.models import (
    ColorConfig,
    RouteDetails,
    NodeDetails,
)
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from math import radians, sin, cos, sqrt, atan2
import osmnx as ox
import networkx as nx
import geopandas as gpd
from geopandas import GeoDataFrame
import folium
import logging


logger = logging.getLogger(__name__)


def unwrap_nested_lists(lst):
    """Unwrap a list of lists into a single list. Used when identifying nodes in a graph by edges"""
    result = []
    for item in lst:
        if isinstance(item, list) or isinstance(item, set):
            result.extend(unwrap_nested_lists(item))
        else:
            result.append(item)
    return result


def get_generic_cmap(num_colors):
    """Get a colormap with 1000 colors"""
    return plt.get_cmap("gist_rainbow", num_colors)


def get_static_color_map():
    cmap = ListedColormap(ColorConfig.colors)
    return cmap


def create_colors(n):
    """Create n colors with an alpha value of 0.5"""
    alpha = 0.5
    hsv_tuples = [(x * 1.0 / n, 1, 1) for x in range(n)]
    rgb_tuples = [tuple(np.round(255 * np.array(hsv_to_rgb(*hsv)), 0).astype(int)) for hsv in hsv_tuples]
    rgba_tuples = [(r, g, b, alpha) for (r, g, b) in rgb_tuples]
    return rgba_tuples


# Calculate the distance between to points or the sphere called earth
def haversine_distance_4326(point1, point2):
    # Convert points to decimal degrees
    lon1, lat1 = point1.x, point1.y
    lon2, lat2 = point2.x, point2.y

    R = 6373.0  # approximate radius of earth in km

    lat1_rad = radians(lat1)
    lon1_rad = radians(lon1)
    lat2_rad = radians(lat2)
    lon2_rad = radians(lon2)

    d_lon = lon2_rad - lon1_rad
    d_lat = lat2_rad - lat1_rad

    a = sin(d_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(d_lon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    distance_between_points = R * c * 1000  # convert to meters
    return distance_between_points


def get_next_node(point, graph, return_dist=True):
    """Get the next node in the graph from a point"""
    x = point.x
    y = point.y
    node = ox.distance.nearest_nodes(graph, X=x, Y=y, return_dist=return_dist)
    return node


def add_points_from_df_to_folium_map(
    folium_map, df: gpd.GeoDataFrame, column: str = "geometry", text: str = ""
) -> folium.Map:
    for idx, row in df.iterrows():
        # Get the lat/lon coordinates of the point
        lat, lon = row[column].coords[0]

        # Create a folium marker for the point and add it to the map
        folium.Marker(location=[lon, lat], popup=folium.Popup(f"{row['osmid']} {text}")).add_to(folium_map)

        # marker_popup_bruder_node = folium.Popup("Brueder Node")
        # marker_icon = 'cloud'

    return folium_map


# Calculate the shortest path between two points (searches for the closest node in the graph for the given points)
def get_shortest_path_from_osmnx(drive_graph, origin_point, destination_point, weight="travel_time") -> dict:
    # weigh is one of travel_time or length
    x_orig = origin_point.x
    y_origin = origin_point.y
    x_dest = destination_point.x
    y_dest = destination_point.y
    origin_node = ox.distance.nearest_nodes(drive_graph, X=x_orig, Y=y_origin)
    destination_node = ox.distance.nearest_nodes(drive_graph, X=x_dest, Y=y_dest)
    route = ox.shortest_path(drive_graph, origin_node, destination_node, weight=weight)

    return {"origin": origin_node, "destination": destination_node, "route": route}


# Get the route details for a given route
def get_route_details_old(route, all_edges, graph):
    edges = GeoDataFrame()
    route_length: int = 999999999999
    travel_time: int = 999999999999
    for i in route:
        edges = edges.append(all_edges.loc[(i, slice(None), slice(None)), :])
        route_length = int(sum(ox.utils_graph.get_route_edge_attributes(graph, route, "length")))
        travel_time = int(sum(ox.utils_graph.get_route_edge_attributes(graph, route, "length")))
    return {"route_length": route_length, "travel_time": travel_time, "edges": edges}


def get_edges_from_route_details(route, all_edges, graph):
    edges_list = []
    for i in route:
        edges_list.append(all_edges.loc[(i, slice(None), slice(None)), :])
        # edges = edges.append(all_edges.loc[(i, slice(None), slice(None)), :])
    route_length = int(sum(ox.utils_graph.get_route_edge_attributes(graph, route, "length")))
    travel_time = int(sum(ox.utils_graph.get_route_edge_attributes(graph, route, "travel_time")))

    edges = GeoDataFrame.from_records(edges_list)
    return {"route_length": route_length, "travel_time": travel_time, "edges": edges}


def get_route_length(route, graph):
    route_length = 0
    # iterate over all node pairs and extract their edge length
    for i in range(len(route) - 1):
        edge_data = graph.get_edge_data(route[i], route[i + 1])
        route_length += edge_data.get(0)["length"]
    return int(route_length)


def calculate_haversine_distance_for_gdf_row(row, external_node_centroid):
    nearest_node_point = row.nearest_node_point
    distance_as_the_crow_flies = haversine_distance_4326(external_node_centroid, nearest_node_point)
    return distance_as_the_crow_flies


def a_star_heuristic(node_a, node_b, graph) -> float:
    logger.warning(
        "WARNING: Current implementation of a_star or thus heuristic seems to be really inefficient and"
        " should be avoided until further evaluation and enhancement"
    )
    x1, y1 = graph.nodes[node_a]["x"], graph.nodes[node_a]["y"]
    point_a = Point(x1, y1)
    x2, y2 = graph.nodes[node_b]["x"], graph.nodes[node_b]["y"]
    point_b = Point(x2, y2)
    distance_as_the_crow_flies = haversine_distance_4326(point_a, point_b)
    return distance_as_the_crow_flies


def calc_routes_for_node_and_services(
    node_index,
    node_centroid,
    services_dataframe,
    street_network,
    service_type,
    edges,
    use_a_star: bool = False,
):
    service_routes: List[RouteDetails] = list()
    # this is a performance optimization to avoid far away routes if shorter ones are already found
    services_distances = services_dataframe.copy()
    services_distances["haversine_distance"] = float(-1)
    services_distances["haversine_distance"] = services_distances.apply(
        calculate_haversine_distance_for_gdf_row,
        axis=1,
        external_node_centroid=node_centroid,
    )
    services_distances = services_distances.sort_values(by="haversine_distance", ascending=True)

    shortest_route_from_node_to_service: float = float("inf")
    for services_index, services_row in services_distances.iterrows():
        nearest_node_id = services_row.nearest_node_id

        nearest_node_point = services_row.nearest_node_point
        service_name = services_row["name"]
        service_osm_id = services_row["osm_id"]
        distance_as_the_crow_flies = services_row["haversine_distance"]

        if distance_as_the_crow_flies > shortest_route_from_node_to_service:
            # distance as the crow flies is longer than the already found shortest route (skip)
            # since the routes are sorted now - we can even stop calculation for this service altogether
            break
        shortest_route_to_service = None
        try:
            if use_a_star:
                # usage of lambda allows passing street network and its information about the nodes to the heuristic
                shortest_route_to_service = astar_path(
                    street_network,
                    nearest_node_id,
                    node_index,
                    heuristic=lambda u, v: a_star_heuristic(u, v, street_network),
                    weight="length",
                )

            else:
                shortest_route_to_service = shortest_path(street_network, nearest_node_id, node_index, weight="length")
        except NetworkXNoPath as no_path_exception:
            logger.debug(
                f"Failed to get shortest route from service node: {service_name}, {nearest_node_id} to sample node: {node_index}, {node_centroid}; Exception: {no_path_exception}"
            )
            if not street_network.has_node(nearest_node_id) or not street_network.has_node(node_index):
                logger.warning(f"Service node existing: {street_network.has_node(nearest_node_id)}")
                logger.warning(f"Sample node existing: {street_network.has_node(node_index)}")
        except Exception as e:
            logger.warning(
                f"Failed to get shortest route from service node: {service_name}, {nearest_node_id} to sample node: {node_index}, {node_centroid}; Exception: {e}"
            )
        route_line = None
        if shortest_route_to_service is None:
            # logger.warning(f"No route found from {service_name} to {node_centroid}")
            route_length = float("inf")
        else:
            # logger.info(f"Route found: {shortest_route_to_service}")
            route_length = get_route_length(shortest_route_to_service, street_network)
            shortest_route_from_node_to_service = route_length

            # get the route as a multilinestring
            route_edges = [
                (shortest_route_to_service[i], shortest_route_to_service[i + 1], 0)
                for i in range(len(shortest_route_to_service) - 1)
            ]
            path_geoms = []
            for edge in route_edges:
                try:
                    geom = edges.at[edge, "geometry"]
                except KeyError:
                    # if index does not exist reverse ((u, v, k) -> (v, u, k)) it and look again
                    reversed_edge = (edge[1], edge[0], edge[2])
                    geom = reverse(edges.at[reversed_edge, "geometry"])
                path_geoms.append(geom)
            route_line = MultiLineString(path_geoms)

        if not service_name:
            service_name = f"{services_row['amenity']}_{service_osm_id}"
        service_route_details_object = RouteDetails(
            from_node_id=nearest_node_id,
            from_name=service_name,
            from_osm_id=service_osm_id,
            from_position=nearest_node_point,
            to_centroid=node_centroid,
            to_node_id=node_index,
            service_type=service_type,
            route_length=route_length,
            route=route_line,
        )
        service_routes.append(service_route_details_object)

    return service_routes


def calculate_routes(
    samples: gpd.GeoDataFrame,
    services: dict[str, gpd.GeoDataFrame],
    street_network: nx.MultiGraph,
    state: str,
    use_multi_source_dijkstra: bool = True,
    use_a_star: bool = False,
) -> List[NodeDetails]:
    if use_multi_source_dijkstra:
        if use_a_star:
            logger.error("Cannot use A* with multi source Dijkstra algorithm")
            return []
        return calculate_routes_multi_dijkstra(samples, services, street_network, state)

    else:
        return calculate_routes_single_source(samples, services, street_network, state, use_a_star=use_a_star)


def calculate_routes_single_source(
    samples: gpd.GeoDataFrame,
    services: gpd.GeoDataFrame,
    street_network: nx.MultiGraph,
    state,
    use_a_star: bool = False,
) -> List[NodeDetails]:
    # for each of the geometries / lineStrings in districts_with_name get all buildings from gdf_residential_buildings
    # that are within the geometry using intersect
    # get the closest fire station for this district
    node_details: List[NodeDetails] = list()

    logger.info(f"Calculating {state} routes for {len(samples)} nodes to service types: {list(services.keys())}")
    # progress_bar = tqdm(total=len(samples), desc="Processing samples")
    edges = ox.graph_to_gdfs(street_network, nodes=False, edges=True)
    sample_count = 0
    for sample_index, sample in samples.iterrows():
        node_point = sample["geometry"]
        service_routes: List[RouteDetails] = list()
        for service_type, service_gdf in services.items():
            routes = calc_routes_for_node_and_services(
                node_index=sample_index,
                node_centroid=node_point,
                services_dataframe=service_gdf,
                street_network=street_network,
                service_type=service_type,
                edges=edges,
                use_a_star=use_a_star,
            )
            service_routes.extend(routes)

        node_details_object = NodeDetails(
            node_point=node_point,
            routes=service_routes,
            node_index=sample_index,
        )
        # adds the all route information (to all services) for the current node to the list
        node_details.append(node_details_object)
        # progress_bar.update(1)
        sample_count += 1
        logger.info(f"Processed sample {sample_count} / {len(samples.index)}")
    # progress_bar.close()
    return node_details


def calculate_routes_multi_dijkstra(
    samples, all_services: dict[str, gpd.GeoDataFrame], street_network, state
) -> List[NodeDetails]:
    # for each of the geometries / lineStrings in districts_with_name get all buildings from gdf_residential_buildings
    # that are within the geometry using intersect
    # get the closest fire station for this district
    node_details: dict[Any, NodeDetails] = dict()

    logger.info(f"Calculating {state} routes for {len(samples)} nodes to service types: {list(all_services.keys())}")
    # progress_bar = tqdm(total=len(samples), desc="Processing samples")
    edges = ox.graph_to_gdfs(street_network, nodes=False, edges=True)
    sample_count = 0
    for service_type, service_by_type_gdf in all_services.items():
        # if len(service_by_type_gdf) == 0:
        #     continue
        """
        Calculate the shortest path between two points (searches for the closest node in the graph for the given points)
        distance = dict key = node_id (target), value = distance between node (target) and service (source)
        path = dict key = node_id (target), value (list of nodes that make out a path) = shortest path from service (source) to node (target)
        """
        distance, path = nx.multi_source_dijkstra(
            street_network,
            sources=set(service_by_type_gdf["nearest_node_id"].unique()),
            weight="length",
        )

        for sample_index, sample in samples.iterrows():
            # BEGIN #################################################################
            #########################################################################
            # route = calc_route_for_node_and_service(...)

            nearest_node_point = None
            service_osm_id = None
            nearest_node_id = None
            service_name = None
            route_line = None
            route_length = float("inf")

            sample_path = path.get(sample_index)
            # test if any path from any service to the current sample was found
            if sample_path:
                # build tuple of nodes that build up the path
                route_edges = [(sample_path[i], sample_path[i + 1], 0) for i in range(len(sample_path) - 1)]
                path_geoms = []
                for edge in route_edges:
                    try:
                        geom = edges.at[edge, "geometry"]
                    except KeyError:
                        # if index does not exist reverse ((u, v, k) -> (v, u, k)) it and look again
                        reversed_edge = (edge[1], edge[0], edge[2])
                        geom = reverse(edges.at[reversed_edge, "geometry"])
                    path_geoms.append(geom)

                nearest_node_id = sample_path[0]
                # based on nears_node_id retrieve service_name, from_osm_id, and from_position via services dataframe
                from_service_row = service_by_type_gdf[service_by_type_gdf["nearest_node_id"] == nearest_node_id]
                from_service_row = from_service_row.iloc[0]
                nearest_node_point = from_service_row["nearest_node_point"]
                service_name = from_service_row["name"]
                if "osm_id" in from_service_row:
                    service_osm_id = from_service_row["osm_id"]
                elif "osmid" in from_service_row:
                    service_osm_id = from_service_row["osmid"]
                else:
                    raise RuntimeError("ERROR: missing expected key osmid in service data")

                route_length = distance.get(sample_index)
                route_line = MultiLineString(path_geoms)

            route = RouteDetails(
                # (from service to sample)
                from_node_id=nearest_node_id,
                from_name=service_name,
                from_osm_id=service_osm_id,
                from_position=nearest_node_point,
                to_centroid=sample["geometry"],
                to_node_id=sample_index,
                service_type=service_type,
                route_length=route_length,
                route=route_line,
            )

            # END ###################################################################
            #########################################################################

            sample_details = node_details.get(sample_index)
            if sample_details:
                sample_details.routes.append(route)
            else:
                node_point = sample["geometry"]
                service_route: List[RouteDetails] = list([route])
                node_details_object = NodeDetails(
                    node_point=node_point,
                    routes=service_route,
                    node_index=sample_index,
                )
                # adds the all route information (to all services) for the current node to the list
                node_details.update({sample_index: node_details_object})
            # progress_bar.update(1)
            sample_count += 1
            logger.info(f"Processed sample {sample_count} / {len(samples.index)}")
    # progress_bar.close()
    return list(node_details.values())


def convert_route_details(to_index, to_centroid, service_routes: List[RouteDetails]):
    list_of_service_station_dicts: List[Dict] = list()

    if len(service_routes) == 0:
        # add NodeDetails for values that have no routes
        print("Still adding values")
        no_route_details = {
            "from_name": "None",
            "from_index": -1,
            "from_centroid": None,
            "to_index": to_index,
            "to_centroid": to_centroid,
            "service_type": None,
            "route_length": -1,
            "travel_time": -1,
        }
        service_station_dict = no_route_details
        list_of_service_station_dicts.append(service_station_dict)
        # raise RuntimeError("There are no routes for this node")

    service_route: RouteDetails
    for service_route in service_routes:
        service_station_dict = service_route.model_dump()
        list_of_service_station_dicts.append(service_station_dict)

    return list_of_service_station_dicts


def create_distance_df(node_details: List[NodeDetails]) -> GeoDataFrame:
    service_dicts = []
    for node in node_details:
        # create the dicts for services
        node_routes = convert_route_details(
            to_index=node.node_index,
            to_centroid=node.node_point,
            service_routes=node.routes,
        )
        service_dicts.extend(node_routes)

    return GeoDataFrame.from_records(service_dicts)


def rename_gdf_id_column(df: DataFrame | GeoDataFrame):
    if "id" in df.columns:
        df.rename(columns={"id": "osm_id"}, inplace=True)
        return df
    elif "osmid" in df.columns:
        df.rename(columns={"osmid": "osm_id"}, inplace=True)
        return df
    else:
        logger.warning("No known column {id, osmid} to rename found")
        return df
