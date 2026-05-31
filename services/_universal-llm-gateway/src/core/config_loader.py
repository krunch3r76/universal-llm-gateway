"""Configuration loader for the Universal LLM Gateway"""

import os
from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

from .catalog import get_catalog_loader
from .gateway_config import GatewayConfig

logger = get_logger(__name__)


def validate_thread_count(value: str | None, param_name: str) -> int | None:
    """
    Validate thread count configuration with bounds checking.

    Args:
        value: Thread count from environment variable
        param_name: Parameter name for error messages

    Returns:
        Validated thread count or None if not set

    Raises:
        ValueError: If thread count is invalid
    """
    if value is None:
        return None

    try:
        threads = int(value)
    except ValueError:
        raise ValueError(f"Invalid {param_name}: '{value}' is not a valid integer")

    # Bounds checking: 1 <= threads <= 256
    if threads < 1:
        raise ValueError(f"Invalid {param_name}: {threads} is below minimum (1)")
    if threads > 256:
        raise ValueError(f"Invalid {param_name}: {threads} exceeds maximum (256)")

    return threads


class ConfigLoader:
    """
    Configuration loader for all gateway configuration files.

    Model Path Root Priority:
        1. MODEL_PATH_ROOT environment variable (highest priority)
        2. model_path_root field in model_loaders.yaml
        3. Exception if neither set (fail-fast)

    Environment Variables:
        MODEL_PATH_ROOT: Override model path root
                        Example: MODEL_PATH_ROOT=/data/models
    """

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.gateway_config: GatewayConfig | None = None
        self.model_loaders_config: dict[str, Any] | None = None
        self.logging_config: dict[str, Any] | None = None

    @staticmethod
    def _get_model_path_root(config: dict[str, Any]) -> str:
        """
        Get model path root with priority: env var → config value → exception.

        Args:
            config: Loaded model configuration (catalog or legacy format)

        Returns:
            Model path root (without trailing slash)

        Raises:
            ValueError: If MODEL_PATH_ROOT env var not set
        """
        # Priority 1: Environment variable (required for catalog-based config)
        model_root = os.getenv("MODEL_PATH_ROOT")
        if model_root:
            return model_root.rstrip("/")

        # Priority 2: Legacy config value (expand ~ for home directory)
        # Kept for backward compatibility during migration
        model_root = config.get("model_path_root")
        if model_root:
            return os.path.expanduser(str(model_root)).rstrip("/")

        # Neither set: throw exception
        raise ValueError(
            "Model path root not configured. Set MODEL_PATH_ROOT environment variable."
        )

    @staticmethod
    def _resolve_model_path(original_path: str, model_root: str) -> str:
        """
        Resolve model path by joining relative paths with model_root.

        Args:
            original_path: Path from model_loaders.yaml (relative or absolute)
            model_root: Model path root (without trailing slash)

        Returns:
            Resolved absolute path

        Examples:
            Relative: "Qwen2.5-Coder-14B-Instruct-Q8_0.gguf"
            model_root: "/data/models"
            Result: "/data/models/Qwen2.5-Coder-14B-Instruct-Q8_0.gguf"

            Relative: "deepseek-ai/deepseek-llm-67b-chat.Q4_K_M.gguf"
            model_root: "/data/models"
            Result: "/data/models/deepseek-ai/deepseek-llm-67b-chat.Q4_K_M.gguf"

            Absolute (backwards compat): "~/.models/model.gguf"
            model_root: "/data/models"
            Result: "/data/models/model.gguf"
        """
        # Handle absolute paths for backwards compatibility
        if original_path.startswith("/") or original_path.startswith("~"):
            # Expand ~ to actual home directory
            expanded_path = os.path.expanduser(original_path)
            # Strip any prefix up to and including '/models/' to extract
            # relative portion
            legacy_roots = ["/mnt/torus/models", os.path.expanduser("~/.models")]
            for default_root in legacy_roots:
                if expanded_path.startswith(default_root):
                    relative_path = expanded_path[len(default_root) :].lstrip("/")
                    return f"{model_root}/{relative_path}"
            # If it's some other absolute path, return as-is (assume user knows
            # what they're doing)
            return expanded_path

        # Relative path - join with model_root
        return f"{model_root}/{original_path}"

    def load_gateway_config(
        self, config_file: str = "gateway_config.yaml"
    ) -> GatewayConfig:
        """Load gateway configuration"""
        config_path = self.config_dir / config_file

        if not config_path.exists():
            raise FileNotFoundError(f"Gateway config file not found: {config_path}")

        try:
            with open(config_path, encoding="utf-8") as f:
                config_data = yaml.safe_load(f)

            self.gateway_config = GatewayConfig(**config_data)
            # logging.info(f"Gateway configuration loaded from {config_path}")
            return self.gateway_config

        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in gateway config: {e}")
        except Exception as e:
            raise ValueError(f"Error loading gateway config: {e}")

    def load_model_loaders_config(
        self, config_file: str = "model_loaders.yaml"
    ) -> dict[str, Any]:
        """
        Load model configuration from model catalog.

        Uses CatalogLoader to load and merge static + local catalogs,
        then converts to the legacy model_loaders format expected by ModelRegistry.

        Args:
            config_file: Ignored - catalog paths are determined by CatalogLoader

        Returns:
            Model configuration in model_loaders.yaml format
        """
        try:
            # Get catalog loader singleton (catalog location determined by
            # workspace root)
            catalog_loader = get_catalog_loader()

            # Load merged catalog and convert to legacy format
            self.model_loaders_config = (
                catalog_loader.get_all_models_as_loaders_format()
            )

            # Validate basic structure
            if "models" not in self.model_loaders_config:
                self.model_loaders_config["models"] = {}

            # Get model path root (env var → exception if not set)
            try:
                model_root = self._get_model_path_root(self.model_loaders_config)
            except ValueError as e:
                logger.error(f"Model path root configuration error: {e}")
                raise

            # Log the source of model_root
            if os.getenv("MODEL_PATH_ROOT"):
                logger.info(f"MODEL_PATH_ROOT from environment: {model_root}")
            else:
                logger.info(f"MODEL_PATH_ROOT from catalog config: {model_root}")

            # Apply model_root to all model paths
            models = self.model_loaders_config.get("models", {})
            for model_key, model_config in models.items():
                if isinstance(model_config, dict):
                    info = model_config.get("info", {})
                    if "path" in info and info["path"]:
                        original_path = info["path"]
                        resolved_path = self._resolve_model_path(
                            original_path, model_root
                        )
                        info["path"] = resolved_path

            model_count = len(self.model_loaders_config.get("models", {}))
            logger.info(f"Model catalog loaded: {model_count} models")
            return self.model_loaders_config

        except Exception as e:
            logger.error(f"Error loading model catalog: {e}")
            raise ValueError(f"Error loading model catalog: {e}")

    def load_logging_config(self, config_file: str = "logging.yaml") -> dict[str, Any]:
        """Load logging configuration with automatic fallback if file doesn't exist"""
        config_path = self.config_dir / config_file

        if not config_path.exists():
            logger.warning(
                f"Logging config file not found: {config_path}. Using basic logging."
            )
            return self._get_default_logging_config()

        try:
            with open(config_path, encoding="utf-8") as f:
                self.logging_config = yaml.safe_load(f)

            # logger.info(f"Logging configuration loaded from {config_path}")
            return self.logging_config or {}

        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in logging config: {e}")
        except Exception as e:
            raise ValueError(f"Error loading logging config: {e}")

    def _get_default_logging_config(self) -> dict[str, Any]:
        """Get default logging configuration"""
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "class": "universal_logging.utc_formatter.UTCFormatter",
                    "format": (
                        "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s"
                    ),
                    "datefmt": "%Y-%m-%dT%H:%M:%SZ",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": "INFO",
                    "formatter": "standard",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"level": "INFO", "handlers": ["console"]},
        }

    def load_all_configs(self) -> tuple[GatewayConfig, dict[str, Any], dict[str, Any]]:
        """
        Load all configuration files.

        Returns:
            Tuple of (gateway_config, model_loaders_config, logging_config)
        """
        gateway_config = self.load_gateway_config()
        model_loaders_config = self.load_model_loaders_config()
        logging_config = self.load_logging_config()

        return gateway_config, model_loaders_config, logging_config


# Global configuration instance
config_loader = ConfigLoader()


def get_config_loader() -> ConfigLoader:
    """Get the global configuration loader instance"""
    return config_loader
