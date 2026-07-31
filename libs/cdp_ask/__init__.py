"""Jupiter CDP project-ask satellite — async execution store + FastAPI surface.

``create_app`` is resolved lazily: it pulls the Playwright-backed runner stack,
which only the satellite host installs. Client-side consumers (Stargate, MCP)
import ``cdp_ask.client`` / ``cdp_ask.models`` and must not require Playwright.
"""

from typing import Any

from .client import CdpAskClient, CdpAskClientError, project_ask_base_url
from .models import (
    FollowupProjectAskRequest,
    FollowupProjectAskResponse,
    SubmitProjectAskRequest,
)

__all__ = [
    "CdpAskClient",
    "CdpAskClientError",
    "FollowupProjectAskRequest",
    "FollowupProjectAskResponse",
    "SubmitProjectAskRequest",
    "create_app",
    "project_ask_base_url",
]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
