import logging
import os

from css_geodata_service.utils import exec_subprocess

logger = logging.getLogger(__name__)


def extract_buildings_from_pbf_to_osmxml(pbf_file_path: str, output_file_path: str, force_update: bool = False):
    """
    Extracts buildings from a pbf file to an osm xml file
    :param pbf_file_path:
    :param output_file_path:
    :param force_update:
    :return:
    """
    if not force_update and os.path.exists(output_file_path):
        logger.warning(f"Skipping {pbf_file_path} because {output_file_path} already exists")
        return

    command = f"osmium tags-filter {pbf_file_path} w/building -o {output_file_path} --overwrite"
    return exec_subprocess(command)
