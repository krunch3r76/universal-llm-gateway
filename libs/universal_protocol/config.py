"""Configuration management for Universal Protocol.

Loads settings from config.yaml with sensible defaults.
Provides a singleton configuration object for the entire protocol.
"""

import copy
import os
from pathlib import Path
from typing import Any

import yaml


class ProtocolConfig:
    """Universal Protocol configuration.

    Loads from config.yaml with defaults for all values.
    """

    # Default configuration values
    DEFAULTS = {
        "protocol": {
            "socket_dir": "/tmp/universal-protocol",
        },
        "server": {
            "loop": "uvloop",
            "workers": 1,
        },
    }

    def __init__(self, config_path: str | None = None):
        """Initialize configuration.

        Args:
            config_path: Path to config.yaml file. If None, uses defaults only.
        """
        # Use deepcopy to avoid modifying class-level DEFAULTS
        self._config = copy.deepcopy(self.DEFAULTS)

        if config_path and Path(config_path).exists():
            self._load_from_file(config_path)

    def _load_from_file(self, config_path: str) -> None:
        """Load configuration from YAML file.

        Args:
            config_path: Path to config.yaml file
        """
        try:
            with open(config_path) as f:
                loaded = yaml.safe_load(f) or {}
                self._merge_config(loaded)
        except Exception as e:
            # If config fails to load, just use defaults
            import logging

            logging.warning(f"Failed to load config from {config_path}: {e}")

    def _merge_config(self, loaded: dict[str, Any]) -> None:
        """Merge loaded config with defaults.

        Args:
            loaded: Configuration loaded from file
        """
        # Deep merge with defaults
        for section, values in loaded.items():
            if section in self._config and isinstance(values, dict):
                self._config[section].update(values)
            else:
                self._config[section] = values

    # Protocol settings
    @property
    def socket_dir(self) -> str:
        """Socket directory path."""
        return self._config["protocol"]["socket_dir"]

    # Server settings
    @property
    def server_loop(self) -> str:
        """Event loop type (uvloop or asyncio)."""
        return self._config["server"]["loop"]

    @property
    def server_workers(self) -> int:
        """Number of server worker processes."""
        return self._config["server"]["workers"]


# Global singleton instance
_config: ProtocolConfig | None = None


def get_config(config_path: str | None = None) -> ProtocolConfig:
    """Get the global configuration instance.

    Args:
        config_path: Path to config.yaml (only used on first call)

    Returns:
        Global ProtocolConfig instance
    """
    global _config
    if _config is None:
        # Look for config.yaml in several standard locations
        if config_path is None:
            search_paths = [
                "config.yaml",
                "libs/universal_protocol/config.yaml",
                "/etc/universal-protocol/config.yaml",
                os.path.expanduser("~/.config/universal-protocol/config.yaml"),
            ]
            for path in search_paths:
                if Path(path).exists():
                    config_path = path
                    break

        _config = ProtocolConfig(config_path)

    return _config
