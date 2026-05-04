import time
from shapely import Point, Polygon, unary_union
import pandas as pd
import geopandas as gpd
from networkx import multi_source_dijkstra
from osmnx.convert import graph_to_gdfs


from css_geodata_service.robustness_of_accessibility.extract_osm import (
    extract_street_network,
    extract_poi,
    extract_boundaries,
)
from css_geodata_service.robustness_of_accessibility.disrupted_graph import (
    get_graph_affected_by_polygon_from_gdfs,
)
from css_geodata_service.robustness_of_accessibility.robustness_of_accessibility import (
    prepare_services,
    calculate_robustness_of_networks,
)


start_init_time = time.time()

# create some arbitrary polygon representing the region of interest, here: a bounding box of trier
print("1: create bbox")
north, south, east, west = 49.85, 49.68, 6.775, 6.55
bbox_trier = Polygon(
    [
        Point(west, south),
        Point(east, south),
        Point(east, north),
        Point(west, north),
        Point(west, south),
    ]
)

# get the street network of the region of interest (in this case as an undirected graph) and administrative boundaries.
print("2: extract network (orig), to_undirected(), administrative boundaries")
network_orig = extract_street_network(osm=None, library="osmnx", polygon=bbox_trier).to_undirected()
administrative_boundaries = extract_boundaries(osm=None, library="osmnx", polygon=bbox_trier)


# data for trier can be downloaded via https://akrima-storage.dfki.uni-trier.de/dataset/osm-data-trier
# get the flooding area
print("3: get flooding area")
base_path: str = "css_geo_data_service/robustness_of_accessibility/data/"
flooding_path_m: str = f"{base_path}input/Flooding/HazardAreas/nz_hazardArea_fluival_L-DE_cropped_trier.geojson"
flooded_area_trier_m = gpd.read_file(flooding_path_m)
flooded_area_trier_geoms_m = flooded_area_trier_m.geometry
flooded_area_trier_multipolygon = unary_union(flooded_area_trier_geoms_m)

# generate affected network
print("4: generate flood network")
nodes, edges = graph_to_gdfs(network_orig)
network_flood = get_graph_affected_by_polygon_from_gdfs(
    nodes,
    edges,
    flooded_area_trier_multipolygon,
    exclude_bridges=True,
    undirected_graph=True,
)

# identify positions of service providers
# (The sample of nodes to test the connectivity will be determined inside "calculate_robustness_of_networks".)
print("5: identify POIs")
fire_stations = extract_poi(osm=None, library="osmnx", polygon=bbox_trier, amenities=["hospital"])
poi = {"fire_stations": fire_stations}

init_done_time = time.time()
execution_time_init = init_done_time - start_init_time

#################################
# calculate distances
sample_distance_in_meters = 50
robustness_values_single_source = calculate_robustness_of_networks(
    bbox_trier,
    network_orig,
    poi,
    administrative_boundaries,
    True,
    network_flood,
    sample_distance_in_meters=sample_distance_in_meters,
    use_multi_source_dijkstra=False,
    use_a_star=False,
)
routes_time_single_source = time.time()

# star
robustness_values_a_star = calculate_robustness_of_networks(
    bbox_trier,
    network_orig,
    poi,
    administrative_boundaries,
    True,
    network_flood,
    sample_distance_in_meters=sample_distance_in_meters,
    use_multi_source_dijkstra=False,
    use_a_star=True,
)
routes_time_a_star = time.time()

robustness_values_multi_source = calculate_robustness_of_networks(
    bbox_trier,
    network_orig,
    poi,
    administrative_boundaries,
    True,
    network_flood,
    sample_distance_in_meters=sample_distance_in_meters,
    use_multi_source_dijkstra=True,
)
routes_time_multi_source = time.time()


execution_time_routes_single_source = routes_time_single_source - init_done_time
execution_time_routes_a_star = routes_time_a_star - routes_time_single_source
execution_time_routes_multi_source = routes_time_multi_source - routes_time_a_star
# calculate RoA scores
# roa_scores = calculate_roa_scores(robustness_values)

# print(roa_scores)

#####################################
test = False
if test:
    print("6: get nodes for POIs")
    pd.options.mode.copy_on_write = True
    for service_type, service_gdf in poi.items():
        service_gdf["position"] = service_gdf["geometry"].centroid
        # prepare the services by adding the id and point of the nearest node in the street network
        poi[service_type] = prepare_services(service_gdf, network_orig)

    print("7: dijkstra")
    distance_orig, path_orig = multi_source_dijkstra(
        network_orig,
        sources=set(poi["fire_stations"]["nearest_node_id"].unique()),
        weight="length",
    )
    distance_flood, path_flood = multi_source_dijkstra(
        network_flood,
        sources=set(poi["fire_stations"]["nearest_node_id"].unique()),
        weight="length",
    )

scores_time = time.time()
execution_time_scores = scores_time - execution_time_routes_multi_source

execution_time_total = scores_time - start_init_time


print(f"execution_time_init: {execution_time_init} seconds")
print(f"execution_time_routes single source: {execution_time_routes_single_source} seconds")
print(f"execution_time_routes A*: {execution_time_routes_a_star} seconds")
print(f"execution_time_routes multi source: {execution_time_routes_multi_source} seconds")
print(f"execution_time_scores: {execution_time_scores} seconds")
print(f"execution_time_total: {execution_time_total} seconds")

print(f"Number of Robustness Values single source: {len(robustness_values_single_source)}")
print(f"Number of Robustness Values a_star: {len(robustness_values_a_star)}")
print(f"Number of Robustness Values multi_source: {len(robustness_values_multi_source)}")

"""
current (old) implementation 
####################################################
execution_time_init: 108.71423506736755 seconds
execution_time_routes: 106.19958090782166 seconds
execution_time_scores: 0.002315998077392578 seconds
execution_time_total: 214.9161319732666 seconds
####################################################



dijkstra with sorted services
####################################################
execution_time_init: 108.26386022567749 seconds
execution_time_routes: 34.77570295333862 seconds
execution_time_scores: 0.002282857894897461 seconds
execution_time_total: 143.041846036911 seconds 
####################################################



A*
####################################################
execution_time_init: 110.57202672958374 seconds
execution_time_routes: 347.3387041091919 seconds
execution_time_scores: 0.002259969711303711 seconds
execution_time_total: 457.91299080848694 seconds
####################################################
"""
