import os
import geopandas as gpd
import matplotlib.pyplot as plt
import fiona
import contextily as cx

home = os.path.expanduser("~")
path_as_string = f"{home}/Documents/GeoData/Story/Karte WS7 StoryV2.gpkg"

layer_names = fiona.listlayers(path_as_string)
print(layer_names)


def extract_story_data_from_gpkg_file(gpkg_file_path: str) -> dict:
    # import data
    layers = {}
    osm_grosse_strassen = gpd.read_file(gpkg_file_path, layer="OSM große Straßen")
    baeckerei = gpd.read_file(gpkg_file_path, layer="Baeckerei")
    terminals_region = gpd.read_file(gpkg_file_path, layer="Terminals Region")
    binnenwasserstrassen_region = gpd.read_file(gpkg_file_path, layer="Binnenwasserstrassen Region")
    bahn_gueterumschlag = gpd.read_file(gpkg_file_path, layer="Bahn_Gueterumschlag")
    bahn = gpd.read_file(gpkg_file_path, layer="Bahn")
    liefermengen_v3 = gpd.read_file(gpkg_file_path, layer="LiefermengenV3")
    liefermengen_v2 = gpd.read_file(gpkg_file_path, layer="LiefermengenV2")

    layers["Story_layer_OSM_grosse_Strassen"] = osm_grosse_strassen
    layers["Story_layer_Baeckerei"] = baeckerei
    layers["Story_layer_Terminals_Region"] = terminals_region
    layers["Story_layer_Binnenwasserstrassen_Region"] = binnenwasserstrassen_region
    layers["Story_layer_Bahn_Gueterumschlag"] = bahn_gueterumschlag
    layers["Story_layer_Bahn"] = bahn
    layers["Story_layer_LiefermengenV3"] = liefermengen_v3
    layers["Story_layer_LiefermengenV2"] = liefermengen_v2
    return layers


def convert_all_layers_to_epsg_4326(dict_of_layers: dict):
    # convert all layers to EPSG:4326
    for layer in dict_of_layers:
        dict_of_layers[layer] = dict_of_layers[layer].to_crs(epsg=4326)
    return dict_of_layers


def plot_all_layers(dict_of_layers: dict):
    base = dict_of_layers["Story_layer_Bahn_Gueterumschlag"].plot(color="blue", alpha=0.3, figsize=(11, 11))
    dict_of_layers["Story_layer_Bahn"].plot(ax=base, color="red", alpha=0.5)
    dict_of_layers["Story_layer_Binnenwasserstrassen_Region"].plot(ax=base, color="green", alpha=0.5)
    dict_of_layers["Story_layer_Terminals_Region"].plot(ax=base, color="yellow", alpha=0.5)
    dict_of_layers["Story_layer_Baeckerei"].plot(ax=base, color="orange", alpha=1)
    dict_of_layers["Story_layer_LiefermengenV3"].plot(ax=base, color="cyan", alpha=0.3)
    cx.add_basemap(base, crs=dict_of_layers["Story_layer_Baeckerei"].crs)

    plt.show()
    print("Done")
