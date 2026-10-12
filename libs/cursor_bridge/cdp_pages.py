"""Debug-port page list for an X11 Electron app.

The bridge driver calls this to find the renderer websocket. ``/json/list``
returns browser pages, not sidebar chats. A page titled ``Grok Bot`` or
``Cursor Agents`` is the window. The active chat title comes from the page
DOM later and is what ``selection_verdict`` compares. No wake paste happens
here.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


def list_pages(base_url: str, *, timeout_s: float = 3.0) -> list[dict[str, str]]:
    """Return ``type=page`` targets from ``{base_url}/json/list``.

    ``base_url`` is the debug port origin, for example
    ``http://127.0.0.1:9335``. Each row has ``id``, ``title``, ``url``, and
    ``web_socket_debugger_url``. Connection failures raise ``URLError``.
    Other target types (browser, worker) are dropped.
    """
    origin = base_url.rstrip("/")
    with urlopen(f"{origin}/json/list", timeout=timeout_s) as resp:
        payload = json.load(resp)
    if not isinstance(payload, list):
        raise URLError(f"json/list returned {type(payload).__name__}, expected list")
    pages: list[dict[str, str]] = []
    for row in payload:
        if not isinstance(row, dict) or row.get("type") != "page":
            continue
        pages.append(
            {
                "id": str(row.get("id") or ""),
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "web_socket_debugger_url": str(row.get("webSocketDebuggerUrl") or ""),
            }
        )
    return pages


def page_summary(pages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Titles and ids only, for logs that must not carry debugger URLs."""
    return [
        {"id": str(page.get("id") or ""), "title": str(page.get("title") or "")}
        for page in pages
    ]
