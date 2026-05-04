import logging
import os
import subprocess
from typing import List

from shapely.geometry import Polygon

logger = logging.getLogger(__name__)


def exec_subprocess(command: str):
    logger.info(f"Running command: {command}")

    try:
        result: subprocess.CompletedProcess = subprocess.run(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )  # timeout=timeout_in_seconds,
    except subprocess.CalledProcessError as exc:
        logger.error("Status : FAIL", exc.returncode, exc.output)
    else:
        logger.info(result)
        return result


def get_home():
    return os.path.expanduser("~")


def get_default_data_path():
    return f"{get_home()}/GeoData"


def get_default_elevation_data_path():
    return f"{get_default_data_path()}/Elevation"


def get_default_flooding_data_path():
    return f"{get_default_data_path()}/Flooding"


def get_default_flooding_risk_zones_path():
    return f"{get_default_flooding_data_path()}/RiskZone"


def get_default_flooding_hazard_areas_path():
    return f"{get_default_flooding_data_path()}/HazardAreas"


def get_default_osm_data_path():
    return f"{get_default_data_path()}/OSM"


def get_default_osm_data_trier_path():
    return f"{get_default_osm_data_path()}/trier.osm_osmium.pbf"


def get_osm_data_weser_bergland_path():
    return f"{get_default_osm_data_path()}/weser_bergland.osm_osmium.pbf"


def get_osm_data_rheinland_palatinate_path():
    return f"{get_default_osm_data_path()}/rheinland-pfalz-latest.osm.pbf"


def get_default_story_data_path():
    return f"{get_default_data_path()}/Story_data"


def get_files_in_directory(
    directory_path: str,
    file_extension_filter: str | None = None,
    get_relative_path: bool = False,
    file_slug_filter: str = None,
):
    files: List[str] = list()
    for file in os.listdir(directory_path):
        if file_extension_filter is None or file.endswith(file_extension_filter):
            if file_slug_filter is not None and file_slug_filter not in file:
                # skip files that do not contain the file slug filter
                print(f"Skipping {file} because it does not contain {file_slug_filter}")
                continue
            if get_relative_path:
                files.append(file)
            else:
                files.append(os.path.join(directory_path, file))
    return files


def get_file_extension(file_name: str):
    if "." not in file_name:
        raise RuntimeError(f"File name {file_name} does not contain a file type")
    return f".{file_name.split('.')[-1]}"


def add_postfix_to_path(file_name: str, postfix: str):
    file_type = get_file_extension(file_name)
    return f"{file_name.replace(file_type, f'{postfix}{file_type}')}"


def add_subdirectory_to_path(filename: str, subdirectory: str, path_is_relative: bool):
    if path_is_relative:
        return f"{subdirectory}/{filename}"
    return f"{os.path.dirname(filename)}/{subdirectory}/{os.path.basename(filename)}"


def get_polygon_weser_bergland():
    return Polygon([(7.2, 53.079), (10.329, 53.079), (10.329, 50.89), (7.2, 50.89)])


def get_polygon_porta_westfalica():
    return Polygon([(8.7, 52.4), (9.3, 52.4), (9.3, 52.15), (8.7, 52.15)])


def get_polygon_trier_city():
    return Polygon([(6.558, 49.835), (6.725, 49.835), (6.725, 49.709), (6.558, 49.709)])


def get_polygon_trier_region():
    return Polygon([(6.55, 49.85), (6.775, 49.85), (6.775, 49.68), (6.55, 49.68)])
