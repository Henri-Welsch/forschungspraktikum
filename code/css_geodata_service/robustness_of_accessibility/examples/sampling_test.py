import time

from extract_osm import extract_street_network
from robustness_of_accessibility import (
    draw_sample,
)
from shapely import Point, Polygon
from osmnx.convert import graph_to_gdfs

print("Start")
start_init_time = time.time()

# create some arbitrary polygon representing the region of interest, here: a bounding box of trier
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
network_orig = extract_street_network(osm=None, library="osmnx", polygon=bbox_trier).to_undirected()


# generate affected network
nodes, edges = graph_to_gdfs(network_orig)
samples = draw_sample(bbox_trier, nodes, sample_distance_in_meters=500)
print(len(samples))
