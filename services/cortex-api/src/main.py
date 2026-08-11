"""Thin compatibility wrapper for the authoritative Cortex API implementation.

The live service runs from `libs/cortex_store`. Ownership and import-seed scans
also root at `libs/cortex_store/` (see ``service_lib_ownership``). Keep this
module as a stable entrypoint for tooling that still references
``services/cortex-api/src/main.py``, but do not add application logic here.
"""

from cortex_store.main import app, create_app

__all__ = ["app", "create_app"]
