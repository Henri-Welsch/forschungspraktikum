import logging
from typing import Callable, List
from shapely.geometry import Polygon
import concurrent.futures

from css_geodata_service.crop.python_tools import crop_gml_file_and_save
from css_geodata_service.utils import (
    get_default_flooding_data_path,
    get_files_in_directory,
    add_postfix_to_path,
    get_polygon_weser_bergland,
    get_polygon_trier_region,
    get_polygon_porta_westfalica,
)

logger = logging.getLogger(__name__)


def get_gml_files_from_directory(directory: str, file_slug_filter: str = None) -> List[str]:
    return get_files_in_directory(
        directory_path=directory,
        file_extension_filter=".gml",
        get_relative_path=False,
        file_slug_filter=file_slug_filter,
    )


def crop_gml_files(
    polygon: Polygon,
    files: List[str],
    output_postfix: str,
    crop_function: Callable[[Polygon, str, str, bool], None],
    output_file_format=".gml",
    overwrite=False,
    file_slug_filter: str | None = None,
) -> List[str]:
    clipped_files: List[str] = []
    if "." not in output_file_format:
        output_file_format = f".{output_file_format}"
    # filter files that do not already contain the postfix
    if file_slug_filter is not None:
        filtered_gml_files = [file for file in files if output_postfix not in file and file_slug_filter in file]
    else:
        filtered_gml_files = [file for file in files if output_postfix not in file and "cropped" not in file]

    with concurrent.futures.ProcessPoolExecutor() as executor:
        for file in filtered_gml_files:
            output_file_path = add_postfix_to_path(file, output_postfix)
            if output_file_format != ".gml":
                output_file_path = output_file_path.replace(".gml", f"{output_file_format}")
            logger.info("submitting crop task for file %s", file)
            executor.submit(crop_function, polygon, file, output_file_path, overwrite)
            clipped_files.append(output_file_path)

    return clipped_files


def crop_gml_files_from_directory(
    polygon: Polygon,
    directory: str,
    output_postfix: str,
    crop_function: Callable[[Polygon, str, str, bool], None],
    output_file_format=".gml",
    overwrite=False,
) -> List[str]:
    logger.info(f"Clipping gml files in {directory}")
    gml_files = get_files_in_directory(directory_path=directory, file_extension_filter=".gml", get_relative_path=False)
    return crop_gml_files(polygon, gml_files, output_postfix, crop_function, output_file_format, overwrite)


def crop_gml_files_from_directory_old(
    polygon: Polygon,
    directory: str,
    output_postfix: str,
    crop_function: Callable[[Polygon, str, str, bool], None],
    output_file_format=".gml",
    overwrite=False,
) -> List[str]:
    logger.info(f"Clipping gml files in {directory}")
    clipped_files: List[str] = []
    if "." not in output_file_format:
        output_file_format = f".{output_file_format}"
    gml_files = get_files_in_directory(directory_path=directory, file_extension_filter=".gml", get_relative_path=False)
    # filter files that to not already contain the postfix
    filtered_gml_files = [file for file in gml_files if output_postfix not in file and "cropped" not in file]

    for file in filtered_gml_files:
        output_file_path = add_postfix_to_path(file, output_postfix)
        if output_file_format != ".gml":
            output_file_path = output_file_path.replace(".gml", f"{output_file_format}")
        crop_function(polygon, file, output_file_path, overwrite)
        clipped_files.append(output_file_path)
    return clipped_files


def crop_flooding_data(
    polygon,
    flooding_data_directory,
    output_postfix,
    crop_function: Callable[[Polygon, str, str, bool], None],
    output_file_format=".gml",
    overwrite=False,
    base_file_slug_filter: str | None = None,
) -> List[str]:
    path_risk_zone = f"{flooding_data_directory}/RiskZone"
    path_hazard_area = f"{flooding_data_directory}/HazardAreas"

    # use one ofs: crop_gml_file_and_save
    new_files = list()

    files_to_crop = get_gml_files_from_directory(path_risk_zone, file_slug_filter=base_file_slug_filter)
    files_to_crop.extend(get_gml_files_from_directory(path_hazard_area, file_slug_filter=base_file_slug_filter))
    new_files = crop_gml_files(
        polygon=polygon,
        files=files_to_crop,
        output_postfix=output_postfix,
        output_file_format=output_file_format,
        overwrite=overwrite,
        crop_function=crop_function,
        file_slug_filter=base_file_slug_filter,
    )

    return new_files


def crop_flooding_data_weser_bergland(output_file_format=".gml", overwrite=False) -> List[str]:
    output_postfix = "_cropped_weser_bergland"
    polygon = get_polygon_weser_bergland()
    return crop_flooding_data(
        polygon,
        get_default_flooding_data_path(),
        output_postfix,
        output_file_format=output_file_format,
        overwrite=overwrite,
        crop_function=crop_gml_file_and_save,
    )


def crop_flooding_data_trier(output_file_format=".gml", overwrite=False) -> List[str]:
    output_postfix = "_cropped_trier"
    polygon = get_polygon_trier_region()
    return crop_flooding_data(
        polygon,
        get_default_flooding_data_path(),
        output_postfix,
        output_file_format=output_file_format,
        overwrite=overwrite,
        crop_function=crop_gml_file_and_save,
        base_file_slug_filter="_cropped_weser_bergland",
    )  # crop_gml_file_and_save #crop_file_using_osmium


def crop_flooding_data_porta_westfalica(output_file_format=".gml", overwrite=False) -> List[str]:
    output_postfix = "_cropped_porta_westfalica"
    polygon = get_polygon_porta_westfalica()
    return crop_flooding_data(
        polygon,
        get_default_flooding_data_path(),
        output_postfix,
        output_file_format=output_file_format,
        overwrite=overwrite,
        crop_function=crop_gml_file_and_save,
        base_file_slug_filter="_cropped_weser_bergland",
    )  # crop_gml_file_and_save #crop_file_using_osmium
