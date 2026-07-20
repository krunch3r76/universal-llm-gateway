"""Model registry package — re-exports ModelRegistry and normalize_model_id."""

from .core import ModelRegistry
from .identifiers import normalize_model_id

__all__ = ["ModelRegistry", "normalize_model_id"]
