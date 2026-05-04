import logging

from css_geodata_service.crop.utils import (
    crop_flooding_data_porta_westfalica,
)
from css_geodata_service.crop.system_tools import crop_osm_data_trier

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )  # , filename="elevation_test_poetry.log", filemode="w")
    crop_flooding_data_porta_westfalica(output_file_format=".geojson", overwrite=True)
    crop_osm_data_trier(overwrite=False)
    logging.info("Done")
