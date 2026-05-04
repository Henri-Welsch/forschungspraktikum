import logging
import colorlog

from pydantic import Field
from pydantic_settings import BaseSettings
from pathlib import Path


class GeodataConfig(BaseSettings):
    LOGGING_LEVEL: int = logging.DEBUG
    LOGGING_FMT: str = Field(
        default="%(log_color)s%(levelname)s:%(reset)-8s %(cyan)s%(asctime)s%(reset)s %(blue)s%(name)s%(reset)s: %(message)s"
    )
    LOGGING_DATE_FMT: str = "%Y-%m-%d %H:%M:%S"

    css_geodata_input_data_path: Path = Field(
        description="Path to the 'Entscheidungen' .excel File that is used to initialized model parameters",
        default=Path("css_geodata_model/assets/css_geodata_plausible_inputs.xlsx"),
    )

    css_geodata_model_output_path: Path = Field(
        description="Path to the 'Entscheidungen' .excel File that is used to initialized model parameters",
        default=Path("css_geodata_model/assets/sample_output.csv"),
    )
    css_geodata_batch_run_output_dir: Path = Field(
        description="Path to the 'Entscheidungen' .excel File that is used to initialized model parameters",
        default=Path("css_geodata_model/assets/batch_runs/"),
    )


css_geodata_config = GeodataConfig()

"""
Configure the root logger. This should be called only once in the application,
ideally at the very beginning of the main script or in a settings module that
is imported early. This sets the base logging level, format, date format, and
configures the output handlers.

The handlers determine where log messages are sent. By adding a StreamHandler 
directly in basicConfig, we ensure that logs are outputted to the console 
(stdout/stderr) by default. This makes it easy to see logs during development 
and for simple deployments without needing to configure handlers separately 
for each logger.

If no handlers are specified in basicConfig, a default StreamHandler targeting
stderr is created. However, explicitly adding it here gives us more control and
makes the configuration more explicit.
"""
handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        fmt=css_geodata_config.LOGGING_FMT,
        datefmt=css_geodata_config.LOGGING_DATE_FMT,
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
)

logging.basicConfig(
    level=css_geodata_config.LOGGING_LEVEL,
    handlers=[handler],
)


def get_geodata_logger(module_name: str, logging_level: int = css_geodata_config.LOGGING_LEVEL):
    """
    Get a logger instance with the given module name. This creates a hierarchical
    logger, allowing for more granular control and filtering of logs.

    :param module_name: The name of the module for which to create the logger (usually __name__).
    :param logging_level: The logging level for this specific logger. This overrides the root logger's level if provided.
    :return: A logger instance.
    """
    module_name = module_name.rsplit(".", 1)[-1]
    logger = logging.getLogger(module_name)
    logger.setLevel(logging_level)
    return logger
