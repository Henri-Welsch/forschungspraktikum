import logging
import time
from typing import List, Dict
import numpy as np
import osmnx as ox
import geopandas as gpd

from shapely.ops import unary_union
from shapely.geometry import Point, Polygon
import css_geodata_service.robustness_of_accessibility.utils.utils as utils
from css_geodata_service.robustness_of_accessibility.utils.models import NodeDetails
import pandas as pd
import random
import networkx as nx


logger = logging.getLogger(__name__)


def get_nearest_node_id(graph, point):
    return ox.distance.nearest_nodes(graph, X=point.x, Y=point.y)


def get_node_point_from_node_id(nodes, node_id):
    return nodes.loc[node_id, "geometry"]


def manually_set_hospital_positions_trier(
    gdf_hospitals: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    # also filters unwanted hospitals
    # todo how to use this in a more general way...

    gdf_hospitals = gdf_hospitals[gdf_hospitals["name"] != "Klinikum Mutterhaus der Borromäerinnen Nord"]
    gdf_hospitals.loc[
        gdf_hospitals["name"] == "Klinikum Mutterhaus der Borromäerinnen Trier",
        "position",
    ] = Point(6.632108, 49.754674)
    gdf_hospitals.loc[
        (gdf_hospitals["name"] == "Krankenhaus der Barmherzigen Brüder Trier"),
        "position",
    ] = Point(6.640020, 49.761562)
    return gdf_hospitals


def prepare_hospitals(gdf_hospitals: gpd.GeoDataFrame, drive_graph_all_private) -> gpd.GeoDataFrame:
    """
    Adds nearset node id and to the hospitals dataframe
    :param gdf_hospitals:
    :param drive_graph_all_private:
    :return:
    """
    gdf_hospitals.rename(columns={"geometry": "geometry_hospital"}, inplace=True)
    hospital_nodes_df = drive_graph_all_private.loc[gdf_hospitals["nearest_node_id"]].reset_index()
    hospital_nodes_df.rename(columns={"osmid": "osmid_node"}, inplace=True)
    hospital_nodes_df = hospital_nodes_df[["osmid_node", "geometry"]]

    # merge dataframes on 'nearest_node_id' and 'osmid' columns
    merged_df = pd.merge(
        gdf_hospitals,
        hospital_nodes_df,
        left_on="nearest_node_id",
        right_on="osmid_node",
    )
    merged_df.rename(columns={"geometry": "nearest_node_point"}, inplace=True)
    # drop unnecessary columns
    merged_df = merged_df.drop(columns=["osmid_node"])

    gdf_hospitals = merged_df

    return gdf_hospitals


def add_postion_to_hospitals_and_filter(
    gdf_hospitals: gpd.GeoDataFrame, hospital_positions: Dict[str, Point]
) -> gpd.GeoDataFrame:
    gdf_hospitals = gdf_hospitals.copy(deep=True)

    # only use hospitals with emergency
    gdf_hospitals = gdf_hospitals.loc[gdf_hospitals["emergency"] == "yes"]
    gdf_hospitals["position"] = None
    for key, value in hospital_positions.items():
        gdf_hospitals.loc[gdf_hospitals["name"] == key, "position"] = value

    # additional region specific manual filter -> needs other solution
    gdf_hospitals = manually_set_hospital_positions_trier(gdf_hospitals)
    # drop hospitals without position
    gdf_hospitals = gdf_hospitals[gdf_hospitals["position"].notna()]
    return gdf_hospitals


def prepare_services(services: gpd.GeoDataFrame, drive_service_graph) -> gpd.GeoDataFrame:
    logger.info("Prepare services")
    services = services.copy(deep=True)
    services["nearest_node_id"] = services["position"].apply(
        lambda point: get_nearest_node_id(point=point, graph=drive_service_graph)
    )

    street_network_nodes, _ = ox.graph_to_gdfs(drive_service_graph)
    services["nearest_node_point"] = services["nearest_node_id"].apply(
        lambda node_id: get_node_point_from_node_id(nodes=street_network_nodes, node_id=node_id)
    )
    return services


def draw_sample(
    polygon: Polygon,
    gdf_nodes_drive_service_graph: gpd.GeoDataFrame,
    number_total_samples: int | None = None,
    sample_distance_in_meters: float | None = None,
    random_seed: int = 42,
):
    # Ensure only one sampling method is provided
    if number_total_samples is not None and sample_distance_in_meters is not None:
        raise RuntimeError("Please provide only one of 'number_total_samples' or 'sample_distance_in_meters'.")
    # Ensure only actually sampling method is provided
    if number_total_samples is None and sample_distance_in_meters is None:
        raise RuntimeError("Please provide one of 'number_total_samples' or 'sample_distance_in_meters'.")

    # Calculate polygon dimensions and area
    most_western, most_southern, most_eastern, most_northern = polygon.bounds

    # Create points for each corner
    bottom_left = Point(most_western, most_southern)  # Bottom-left corner
    # bottom_right = Point(most_eastern, most_southern)  # Bottom-right corner
    top_right = Point(most_eastern, most_northern)  # Top-right corner
    top_left = Point(most_western, most_northern)  # Top-left corner

    height_in_m = utils.haversine_distance_4326(top_left, bottom_left)
    width_in_m = utils.haversine_distance_4326(top_left, top_right)

    width_in_degree = most_eastern - most_western
    height_in_degree = most_northern - most_southern
    aspect_ratio = width_in_degree / height_in_degree

    # Determine the number of samples
    if number_total_samples is not None:
        # Compute the grid dimensions based on samples
        number_of_steps_height = int(np.sqrt(number_total_samples / aspect_ratio))
        number_of_steps_width = int(number_of_steps_height * aspect_ratio)

    elif sample_distance_in_meters is not None:
        # num_samples = int(polygon_area * sample_distance_in_meters)
        number_of_steps_height = int(height_in_m / sample_distance_in_meters)
        number_of_steps_width = int(width_in_m / sample_distance_in_meters)
        logger.debug(f"height_in_m {height_in_m} - number_of_steps_height {number_of_steps_height}")
        logger.debug(f"width_in_m {width_in_m} - number_of_steps_width {number_of_steps_width}")

    else:
        raise ValueError("You must specify either 'number_total_samples' or 'sample_density_in_meters'.")

    step_size_height = height_in_degree / number_of_steps_height
    step_size_width = width_in_degree / number_of_steps_width

    cells_without_sample_in_all_graph = 0

    # filtering the nodes before drawing the samples ensures that while the road network can span beyond the
    # investigated region, only samples from that area are used
    filtered_nodes = gdf_nodes_drive_service_graph[gdf_nodes_drive_service_graph.geometry.within(polygon)]

    filtered_nodes["x"] = filtered_nodes["geometry"].x
    filtered_nodes["y"] = filtered_nodes["geometry"].y
    y_sorted_nodes = filtered_nodes.sort_values(by=["y"], ascending=True, inplace=False)
    y_sorted_nodes.reset_index(inplace=True)

    random.seed(random_seed)

    y_index = 0
    grid_samples = list()
    for i in range(number_of_steps_height):
        y_limit = most_southern + (i + 1) * step_size_height  # most_southern bspw.: bbox_south: float = 49.746787
        points_per_row = list()
        while y_index < len(y_sorted_nodes) and y_sorted_nodes.loc[y_index]["y"] < y_limit:
            points_per_row.append(y_sorted_nodes.iloc[y_index])
            y_index += 1

        points_per_row = sorted(points_per_row, key=lambda row: row.x)
        x_index = 0
        for j in range(number_of_steps_width):
            x_limit = most_western + (j + 1) * step_size_width
            points_per_cell = list()

            while x_index < len(points_per_row) and points_per_row[x_index]["x"] < x_limit:
                points_per_cell.append(points_per_row[x_index])
                x_index += 1

            if len(points_per_cell) > 0:
                # found: bool = False
                random_sample = random.sample(points_per_cell, 1)
                grid_samples.append(random_sample[0]["osmid"])
                # sample_number = 1
            else:
                cells_without_sample_in_all_graph += 1
    df_node_samples = filtered_nodes.loc[grid_samples]
    logger.debug(f"#total samples {len(df_node_samples)}")
    return df_node_samples


def filter_samples_by_administrative_boundaries(samples, boundaries):
    districts_filtered = boundaries[boundaries["name"].notnull()]
    # filter samples to only those that are within a considered administrative adrea
    combined_district_polygon = unary_union(districts_filtered.geometry)

    samples["within_polygon"] = samples["geometry"].apply(lambda point: combined_district_polygon.contains(point))
    samples_within_polygon = samples[samples["within_polygon"]].drop(labels="within_polygon", axis=1)
    number_of_all_samples = len(samples)
    number_of_filtered_samples = len(samples_within_polygon)
    if number_of_all_samples != number_of_filtered_samples:
        logger.warning(
            f"Dropped {number_of_all_samples - number_of_filtered_samples} "
            f"samples when applying administrative boundaries.\n"
            f" number of samples = {number_of_all_samples},"
            f" number of (filtered) samples = {number_of_filtered_samples}"
        )

    return samples_within_polygon


def calculate_robustness_of_networks(
    samping_polygon: Polygon,
    street_network_original: nx.MultiDiGraph,
    street_network_flooding: nx.MultiDiGraph,
    poi: dict[str, gpd.GeoDataFrame],
    undirected_graph: bool,
    sample_boundaries: gpd.GeoDataFrame | None = None,
    use_multi_source_dijkstra: bool = True,
    use_a_star: bool = False,
    number_total_samples: int | None = None,
    sample_distance_in_meters: float | None = None,
) -> dict[str, dict[str, gpd.GeoDataFrame]]:
    """
    Calculates the robustness of the network.
    :return: nested dict with form {"normal": {"fire_station": gpd.GeoDataFrame, ...}, "disrupted": {...}}
    """

    if use_a_star and use_multi_source_dijkstra:
        logger.error("Cannot use A* and Multisource at the same time")
        return {}

    start = time.time()
    logging.info("Calculate RoA distances and scores")
    if undirected_graph:
        street_network_original = street_network_original.to_undirected()
        street_network_flooding = street_network_flooding.to_undirected()

    street_network_nodes, street_network_edges = ox.graph_to_gdfs(street_network_original)

    # draw sample to calculate distances to services
    samples = draw_sample(
        polygon=samping_polygon,
        gdf_nodes_drive_service_graph=street_network_nodes,
        number_total_samples=number_total_samples,
        sample_distance_in_meters=sample_distance_in_meters,
    )
    ##
    if sample_boundaries is not None:
        samples = filter_samples_by_administrative_boundaries(samples, sample_boundaries)

    pd.options.mode.copy_on_write = True
    for service_type, service_gdf in poi.items():
        # TODO fix UserWarning: Geometry is in a geographic CRS. Results from 'centroid' are likely incorrect.
        #  Use 'GeoSeries.to_crs()' to re-project geometries to a projected CRS before this operation.
        #  service_gdf["position"] = service_gdf["geometry"].centroid
        service_gdf["position"] = service_gdf["geometry"].centroid
        # prepare the services by adding the id and point of the nearest node in the street network
        poi[service_type] = prepare_services(service_gdf, street_network_original)

    results: dict[str, dict] = {"normal": {}, "disrupted": {}}
    for state in results.keys():
        s = time.time()
        network = None
        if state == "normal":
            network = street_network_original
        if state == "disrupted":
            network = street_network_flooding
        # calculate routes to services in both states
        node_details: List[NodeDetails] = utils.calculate_routes(
            samples=samples,
            services=poi,
            street_network=network,
            state=state,
            use_a_star=use_a_star,
            use_multi_source_dijkstra=use_multi_source_dijkstra,
        )
        logger.info(f"{round((time.time() - s) / 60, 2)} minutes elapsed for {state} street network")
        distances = utils.create_distance_df(node_details)
        # split up service types
        for service_type in distances["service_type"].unique():
            results[state][service_type] = distances[distances["service_type"] == service_type]
    end = time.time()
    logger.info(f"Calculate RoA elapsed time: {end - start}")
    return results


def calculate_roa_scores(distances: dict[str, dict[str, gpd.GeoDataFrame]]):
    """
    {"normal": {"hospital": GeoDatFrame, "fire_station": GeoDatFrame}, "disrupted": {"hospital": GeoDatFrame,  "fire_station": GeoDatFrame}}
    """
    logger.info("Calculating RoA scores for given distances")
    scores = {}
    # loop over normal service_types (could also loop over disrupted service_types as they are the same)
    for service_type in distances["normal"]:
        normal_service_distances = distances["normal"][service_type]
        filtered_normal_distances = normal_service_distances.loc[
            normal_service_distances.groupby("to_node_id")["route_length"].idxmin()
        ]

        disrupted_service_distances = distances["disrupted"][service_type]
        filtered_disrupted_distances = disrupted_service_distances.loc[
            disrupted_service_distances.groupby("to_node_id")["route_length"].idxmin()
        ]

        merged_distances = pd.merge(
            filtered_normal_distances,
            filtered_disrupted_distances[["to_node_id", "route_length", "route"]],
            on="to_node_id",
            suffixes=("_normal", "_disrupted"),
            how="left",
        )
        merged_distances["score"] = merged_distances["route_length_normal"] / merged_distances["route_length_disrupted"]
        merged_distances.fillna({"route_length_disrupted": float("inf")}, inplace=True)
        # fill NaN scores when sample node = service node (score = 0 / 0)
        merged_distances.fillna({"score": 1}, inplace=True)
        merged_distances.loc[merged_distances["score"] > 1, "score"] = 1
        merged_distances.loc[np.isinf(merged_distances["route_length_disrupted"]), "score"] = 0
        scores[service_type] = merged_distances
    return scores
