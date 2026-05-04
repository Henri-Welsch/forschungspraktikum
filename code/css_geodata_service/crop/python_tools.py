import os

import geopandas as gpd
from shapely.geometry import Polygon
import logging

logger = logging.getLogger(__name__)


def crop_gml_file_using_geopandas(polygon: Polygon, gml_file_path: str) -> gpd.GeoDataFrame:
    logging.info(f"Clipping {gml_file_path} with {polygon}")
    df = gpd.read_file(gml_file_path)
    df_clipped = df.clip(polygon)
    return df_clipped


def crop_gml_file_and_save(polygon: Polygon, input_file_path: str, output_file_path, overwrite: bool = False) -> None:
    if not overwrite and os.path.exists(output_file_path):
        logging.info(f"Skipping {input_file_path} because {output_file_path} already exists")
        return

    driver: str
    if output_file_path.endswith(".gml"):
        driver = "GML"
    elif output_file_path.endswith(".shp"):
        driver = "ESRI Shapefile"
    elif output_file_path.endswith(".geojson"):
        driver = "GeoJSON"
    else:
        logging.warning(f"Unknown file format for {output_file_path}. Skipping file")
        return

    # crop the file
    logging.info(f"Going to clip {input_file_path} with {polygon}")
    df_clipped = crop_gml_file_using_geopandas(polygon, input_file_path)

    logging.info(f"Going to save {output_file_path} after clipping")
    df_clipped.to_file(output_file_path, driver=driver)
    logging.info(f"Saved {output_file_path}")
