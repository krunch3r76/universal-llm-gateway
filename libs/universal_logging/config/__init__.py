"""Default configurations for universal logging"""

from pathlib import Path

# Get the directory containing this file
CONFIG_DIR = Path(__file__).parent


def get_default_config_path() -> str:
    """Get the path to the default configuration file."""
    return str(CONFIG_DIR / "default.yaml")


def get_config_path(config_name: str) -> str:
    """Get the path to a specific configuration file."""
    return str(CONFIG_DIR / f"{config_name}.yaml")
