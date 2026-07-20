"""Catalog API endpoints package-shadow of get.py.

Re-exports the shared router so existing imports such as
`from src.routers.api.v1.catalog import get` and `get.router` keep working
after the module split into queries, mutations, schemas, and deps.
"""

from . import mutations as _mutations  # noqa: F401 — register routes
from . import queries as _queries  # noqa: F401 — register routes
from .deps import router

__all__ = ["router"]
