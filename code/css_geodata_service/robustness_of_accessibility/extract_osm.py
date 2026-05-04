import logging
import osmnx as ox
from pyrosm import pyrosm

from css_geodata_service.robustness_of_accessibility.utils.utils import (
    rename_gdf_id_column,
)

logger = logging.getLogger(__name__)


def extract_street_network(osm: pyrosm.OSM | None, library: str = "pyrosm", polygon=None):
    if library == "osmnx":
        return ox.graph_from_polygon(polygon, network_type="drive_service")
    elif library == "pyrosm":
        if osm is None:
            raise RuntimeError("Can not use pyrosm if no osm object is provided")
        nodes, edges = osm.get_network(network_type="driving+service", nodes=True)
        return osm.to_graph(nodes=nodes, edges=edges, network_type="networkx")
    else:
        logger.error(f"Unknown library: {library}")
        return None


def extract_boundaries(osm: pyrosm.OSM | None, library: str = "pyrosm", polygon=None):
    if library == "osmnx":
        boundaries = ox.features_from_polygon(polygon=polygon, tags={"boundary": ["administrative"]})
        boundaries = boundaries[["geometry", "name", "boundary", "admin_level"]]
        boundaries.reset_index(inplace=True)
        return rename_gdf_id_column(boundaries)
    elif library == "pyrosm":
        if osm is None:
            raise RuntimeError("Can not use pyrosm if no osm object is provided")
        boundaries = osm.get_boundaries(boundary_type="administrative")
        return rename_gdf_id_column(boundaries)
    else:
        logger.error(f"Unknown library: {library}")
        return None


def extract_railways(osm: pyrosm.OSM | None, library: str = "pyrosm", polygon=None):
    if library == "osmnx":
        railways = ox.features_from_polygon(polygon, tags={"railway": "rail"})
        railways.reset_index(inplace=True)
        return rename_gdf_id_column(railways)
    elif library == "pyrosm":
        if osm is None:
            raise RuntimeError("Can not use pyrosm if no osm object is provided")
        railways = osm.get_data_by_custom_criteria(custom_filter={"railway": ["rail"]})
        return rename_gdf_id_column(railways)
    else:
        logger.error(f"Unknown library: {library}")
        return None


def extract_waterways(osm: pyrosm.OSM | None, library: str = "pyrosm", polygon=None):
    if library == "osmnx":
        waterways = ox.features_from_polygon(polygon, tags={"waterway": ["river", "canal"]})
        waterways.reset_index(inplace=True)
        return rename_gdf_id_column(waterways)
    elif library == "pyrosm":
        if osm is None:
            raise RuntimeError("Can not use pyrosm if no osm object is provided")
        waterways = osm.get_data_by_custom_criteria(custom_filter={"waterway": ["river", "canal"]})
        return rename_gdf_id_column(waterways)
    else:
        logger.error(f"Unknown library: {library}")
        return None


def extract_buildings(osm: pyrosm.OSM | None, library: str = "pyrosm", polygon=None):
    if library == "osmnx":
        buildings = ox.features_from_polygon(polygon, tags={"building": ["yes"]})
        buildings = buildings[
            [
                "geometry",
                "amenity",
                "addr:postcode",
                "addr:city",
                "addr:street",
                "addr:housenumber",
            ]
        ]
        buildings.reset_index(inplace=True)
        return rename_gdf_id_column(buildings)
    elif library == "pyrosm":
        if osm is None:
            raise RuntimeError("Can not use pyrosm if no osm object is provided")
        buildings = osm.get_buildings(custom_filter={"building": ["yes"]})
        return rename_gdf_id_column(buildings)
    else:
        logger.error(f"Unknown library: {library}")
        return None


def extract_poi(
    osm: pyrosm.OSM | None,
    library: str = "pyrosm",
    polygon=None,
    amenities: list | None = None,
):
    # tags taken from https://wiki.openstreetmap.org/wiki/Key:amenity
    if amenities is None or len(amenities) == 0:
        amenities = [
            "fire_station",
            "hospital",
            "clinic",
            "police",
            "waste_disposal",
            "kindergarten",
            "school",
        ]
    if library == "osmnx":
        try:
            pois = ox.features_from_polygon(polygon, tags={"amenity": amenities})
            pois.reset_index(inplace=True)
            return rename_gdf_id_column(pois)
        except (
            Exception
        ) as e:  # osmnx._errors_InsufficientResponseError (when no osm elements in response for given polygon)
            logger.error(f"Error reading OSMnx features from polygon: {e}")
    elif library == "pyrosm":
        if osm is None:
            raise RuntimeError("Can not use pyrosm if no osm object is provided")
        pois = osm.get_pois(custom_filter={"amenity": amenities})
        return rename_gdf_id_column(pois)
    else:
        logger.error(f"Unknown library: {library}")
        return None


def extract_leisure(osm: pyrosm.OSM | None, library: str = "pyrosm", polygon=None):
    # tags taken from https://wiki.openstreetmap.org/wiki/Key:amenity
    amenities = [
        "bar",
        "biergarten",
        "cafe",
        "fast_food",
        "food_court",
        "ice_cream",
        "pub",
        "restaurant",
        "college",
        "dancing_school",
        "library",
        "surf_school",
        "toy_library",
        "training",
        "music_school",
        "university",
        "boat_rental",
        "boat_sharing",
        "arts_centre",
        "brothel",
        "casino",
        "cinema",
        "community_centre",
        "conference_centre",
        "events_venue",
        "exhibition_centre",
        "gambling",
        "love_hotel",
        "music_venue",
        "nightclub",
        "social_centre",
        "stage",
        "stripclub",
        "swingerclub",
        "theatre",
        "bbq",
        "animal_training",
        "dive_centre",
        "internet_cafe",
        "lounger",
        "marketplace",
        "public_bath",
    ]
    if library == "osmnx":
        try:
            leisure_locations = ox.features_from_polygon(polygon, tags={"amenity": amenities})
            leisure_locations.reset_index(inplace=True)
            return rename_gdf_id_column(leisure_locations)
        except Exception as e:
            logger.error(f"Error reading OSMnx features from polygon: {e}")
    elif library == "pyrosm":
        if osm is None:
            raise RuntimeError("Can not use pyrosm if no osm object is provided")
        leisure_locations = osm.get_pois(custom_filter={"amenity": amenities})
        return rename_gdf_id_column(leisure_locations)
    else:
        logger.error(f"Unknown library: {library}")
        return None
