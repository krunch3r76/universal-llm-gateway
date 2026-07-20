"""Configuration manager for model_loaders.yaml with validation and atomic writes.

Package-shadow of config_manager.py. Re-exports ConfigManager, merge helpers,
and error/result types so existing imports keep working:
`from src.core.config_manager import ConfigManager, deep_merge_dict, ConfigValidationError`.
"""

from .manager import ConfigManager
from .merge import deep_merge_dict
from .types import ConfigValidationError, ModelOperationResult, ValidationContext

__all__ = [
    "ConfigManager",
    "ConfigValidationError",
    "ModelOperationResult",
    "ValidationContext",
    "deep_merge_dict",
]
