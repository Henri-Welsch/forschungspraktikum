import os.path
import subprocess
import logging
from shapely.geometry import Polygon

from css_geodata_service.utils import (
    exec_subprocess,
    get_osm_data_rheinland_palatinate_path,
    get_default_osm_data_trier_path,
)

logger = logging.getLogger(__name__)

"""
For Information on how to crop or filter data see:
https://docs.opentripplanner.org/en/v2.2.0/Preparing-OSM/
"""


def crop_file_using_osmium(polygon: Polygon, input_file_path: str, output_file_path: str, overwrite=False):
    """
    Crops a big pbf file with a polygon
    :param overwrite:
    :param polygon:
    :param input_file_path:
    :param output_file_path:
    :return:
    """

    file_extension_in = os.path.splitext(input_file_path)[1]
    file_extension_out = os.path.splitext(output_file_path)[1]
    if file_extension_in != file_extension_out:
        logger.warning("File extension of Input and output do not match. Output might not be in the correct format")

    if not overwrite and os.path.exists(output_file_path):
        logger.warning(f"Skipping {input_file_path} because {output_file_path} already exists")
        return

    bounds = ",".join([str(x) for x in polygon.bounds])

    if file_extension_out == ".gml":
        command = (
            f"osmium extract --strategy complete_ways --bbox {bounds} {input_file_path} -F gml -o {output_file_path}"
        )
    elif file_extension_out == ".geojson":
        command = f"osmium extract --strategy complete_ways --bbox {bounds} {input_file_path} -F gml -f geojson -o {output_file_path}"
    elif file_extension_out == ".shp":
        raise ValueError(f"File extension {file_extension_out} or not configured supported")
        # command = f"osmium extract --strategy complete_ways --bbox {bounds} {input_file_path} -o {output_file_path}"
    elif file_extension_out == ".pbf":
        command = f"osmium extract --strategy complete_ways --bbox {bounds} {input_file_path} -o {output_file_path}"
    else:
        raise ValueError(f"File extension {file_extension_out} or not configured supported")

    if overwrite:
        command = f"{command} --overwrite"

    exec_subprocess(command)


def crop_osm_data_trier(overwrite: bool = False):
    """
    Crops the flooding data for trier
    :return:
    """
    from css_geodata_service.utils import get_polygon_trier_region

    # Read OSM Data germany and crop it to Trier and save it
    trier_data_selection = get_polygon_trier_region()
    osm_data_rheinland_palatinate_path: str = get_osm_data_rheinland_palatinate_path()
    osm_data_trier_path: str = get_default_osm_data_trier_path()

    crop_file_using_osmium(
        polygon=trier_data_selection,
        input_file_path=osm_data_rheinland_palatinate_path,
        output_file_path=osm_data_trier_path,
        overwrite=overwrite,
    )


def crop_tif_file_using_gdal(polygon: Polygon, tif_file_path: str, output_file_path: str):
    """
    :param polygon: polygon to crop with
    :param tif_file_path: path to tif file
    :param output_file_path: path to output file
    :return:
    """
    # todo this is not tested
    output = subprocess.getoutput(
        f"gdalwarp -cutline {polygon.bounds} -crop_to_cutline {tif_file_path} {output_file_path}"
    )

    logger.info(output)
    return output
