"""Resource History API - Query resource usage history for models.

Package-shadow of resource_history.py. Re-exports `router` with endpoints for
model resource snapshots, historical VRAM/RAM usage, and monitoring status.
"""

from . import history as _history  # noqa: F401 — register routes
from . import monitoring as _monitoring  # noqa: F401 — register routes
from . import stats as _stats  # noqa: F401 — register routes
from .deps import router

__all__ = ["router"]
