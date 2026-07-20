"""Model Configuration Management API Router.

Provides HTTP endpoints for programmatic model catalog management.
Secured by gateway config and optional token authentication.

Package-shadow of management.py — re-exports `router` so
`from src.routers.api.v1.models import management` and
`management.router` keep working via app_factory.
"""

from . import mutations as _mutations  # noqa: F401 — register routes
from . import queries as _queries  # noqa: F401 — register routes
from .deps import router

__all__ = ["router"]
