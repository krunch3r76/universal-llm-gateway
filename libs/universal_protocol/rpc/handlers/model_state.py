"""Global model state for RPC handlers.

Single responsibility: Track loaded models.
"""

from typing import Any

# Global state for loaded models
LOADED_MODELS: dict[str, dict[str, Any]] = {}

