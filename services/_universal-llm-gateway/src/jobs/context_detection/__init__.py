"""Context detection and activation logic for measurement jobs — package-shadow.

Re-exports public helpers so existing `from ..context_detection import …` imports
from measurement modules keep working after the module split.
"""

from .catalog_lock import catalog_write_lock
from .catalog_writes import (
    schedule_remote_sync_nowait,
    update_local_catalog_contexts,
    update_local_catalog_profile,
)
from .constants import REMOTE_SYNC_ENABLED, REMOTE_SYNC_TIMEOUT_MS, STANDARD_CONTEXTS
from .context_lists import (
    determine_activated_contexts,
    get_embedding_contexts,
    get_step_down_contexts,
)
from .gguf_metadata import extract_training_context_from_gguf
from .path_resolution import resolve_model_path
from .training_context import get_training_context

__all__ = [
    "REMOTE_SYNC_ENABLED",
    "REMOTE_SYNC_TIMEOUT_MS",
    "STANDARD_CONTEXTS",
    "catalog_write_lock",
    "determine_activated_contexts",
    "extract_training_context_from_gguf",
    "get_embedding_contexts",
    "get_step_down_contexts",
    "get_training_context",
    "resolve_model_path",
    "schedule_remote_sync_nowait",
    "update_local_catalog_contexts",
    "update_local_catalog_profile",
]
