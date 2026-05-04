import logging
import geopandas as gpd

from css_geodata_service.extract.system_tools import (
    extract_buildings_from_pbf_to_osmxml,
)
from css_geodata_service.utils import (
    get_default_osm_data_path,
    get_default_flooding_hazard_areas_path,
    get_osm_data_weser_bergland_path,
)
from css_geodata_service.extract.python_tools import filter_gdf_from_from_gdf_filter_own

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s:  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def extract_example():
    # todo include data generation step i.e. filter of buildings from pbf file preferably using osmium
    osm_data_path: str = get_default_osm_data_path()
    # todo should use all buildings instead of residential buildings
    path_osm_buildings = f"{osm_data_path}/weser_bergland_residential_buildings_pyrosm.gpkg"
    flooding_hazard_areas_data_path: str = get_default_flooding_hazard_areas_path()
    path_flooding_hazard_areas: str = (
        flooding_hazard_areas_data_path + "/nz_hazardArea_fluival_L-DE_cropped_weser_bergland.gml"
    )

    logger.info("Loading residential buildings")
    residential_buildings = gpd.read_file(path_osm_buildings, layer="residential_buildings")
    logger.info("Loading flooding hazard areas")
    flooding_data_hazard_areas = gpd.read_file(path_flooding_hazard_areas)
    logger.info("Filtering buildings from flooding hazard areas")
    filtered_buildings = filter_gdf_from_from_gdf_filter_own(residential_buildings, flooding_data_hazard_areas)
    percentage_flooded = len(filtered_buildings) / len(residential_buildings) * 100
    print(f"{percentage_flooded:.2f}% of buildings are affected")


def extract_buildings_from_pbf():
    # xml(pbf_file_path: str, output_file_path: str, force_update: bool = False):
    osm_data_path_weser = get_osm_data_weser_bergland_path()
    default_osm_dir = get_default_osm_data_path()
    extract_buildings_from_pbf_to_osmxml(
        pbf_file_path=osm_data_path_weser,
        output_file_path=f"{default_osm_dir}/Buildings/all_buildings_weser_bergland.osm",
        force_update=True,
    )


if __name__ == "__main__":
    extract_buildings_from_pbf()
    # extract_example()
