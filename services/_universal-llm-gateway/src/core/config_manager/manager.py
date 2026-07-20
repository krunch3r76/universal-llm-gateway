"""ConfigManager facade for model_loaders.yaml lifecycle.

Composes validation, I/O, and CRUD mixins into the public ConfigManager class
used by scripts/model_manager.py and hot-reload paths. Thread-safe writes use
FileLock; validation errors raise ConfigValidationError.
"""

from pathlib import Path

from universal_logging import get_logger

try:
    from ...utils.examples import ExampleGenerator
except ImportError:
    from src.utils.examples import ExampleGenerator

from .crud import ConfigCRUDMixin
from .io import ConfigIOMixin
from .validation import ConfigValidationMixin

logger = get_logger(__name__)


class ConfigManager(ConfigIOMixin, ConfigValidationMixin, ConfigCRUDMixin):
    """Centralized manager for model_loaders.yaml with validation and atomic writes.

    Created with a config file path. Load/validate before CRUD. Writes are atomic
    via FileLock. Hot-reload refreshes the in-memory document from disk.
    """

    def __init__(self, config_path: str | Path = "config/model_loaders.yaml"):
        """
        Initialize configuration manager.

        Args:
            config_path: Path to model_loaders.yaml file
        """
        self.config_path = Path(config_path)
        self.lock_path = self.config_path.with_suffix(".yaml.lock")
        self.example_generator = ExampleGenerator()
