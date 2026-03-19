"""Cortex Workbench — serve built SPA at /workbench.

Production build lives in services/cortex-workbench-app/dist/ (output of
``npm run build`` with base="/workbench/"). Mount is conditional — if dist/
does not exist (dev mode), the route is silently skipped.

Dev mode: run ``npm run dev`` in services/cortex-workbench-app/ directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.staticfiles import StaticFiles
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger(__name__)

_DIST_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "cortex-workbench-app"
    / "dist"
)


def mount_workbench(app: FastAPI) -> None:
    """Mount the workbench SPA if the production build exists.

    Must be called after all router registrations (StaticFiles mounts
    catch all paths under the prefix).
    """
    if not _DIST_DIR.exists():
        logger.info("Workbench dist/ not found at %s — skipping mount", _DIST_DIR)
        return

    app.mount(
        "/workbench",
        StaticFiles(directory=str(_DIST_DIR), html=True),
        name="workbench",
    )
    logger.info("Workbench mounted at /workbench from %s", _DIST_DIR)
