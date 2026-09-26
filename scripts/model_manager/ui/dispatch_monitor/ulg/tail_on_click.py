"""Click-time TailPort. The view polls one dispatch and holds the cursor.

Nothing returned here enters the fold. Cursor-sdk reads the drain's retained
lines. CDP reads completed turns from the existing harvest route.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from transport_utils import make_sync_client

from scripts.model_manager.ui.dispatch_monitor.ulg.lease_snapshot import worker_url

_SDK_KINDS = frozenset({"cursor-sdk", "sdk"})
_CDP_KINDS = frozenset({"cdp", "cse"})


def _empty(cursor: int, source: str, error: str) -> dict[str, Any]:
    return {
        "lines": [],
        "cursor": cursor,
        "eof": True,
        "source": source,
        "error": error,
    }


class TailOnClick:
    """Graft adapter for :class:`~dispatch_monitor.core.protocols.TailPort`."""

    def __init__(
        self,
        *,
        sdk_fetch: Callable[[str, int], Mapping[str, Any]] | None = None,
        cdp_fetch: Callable[[str, int], Mapping[str, Any]] | None = None,
    ) -> None:
        self._sdk_fetch = sdk_fetch or _fetch_sdk
        self._cdp_fetch = cdp_fetch or _fetch_cdp

    def tail(self, kind: str, key: str, cursor: int) -> Mapping[str, Any]:
        """Return lines after ``cursor``. Never raises."""
        try:
            if kind in _SDK_KINDS:
                return _normalize_sdk(self._sdk_fetch(key, cursor), cursor)
            if kind in _CDP_KINDS:
                return _normalize_cdp(self._cdp_fetch(key, cursor), cursor)
            return _empty(cursor, kind or "unsupported", "unsupported_kind")
        except Exception as exc:  # noqa: BLE001 — the port never raises
            return _empty(cursor, kind or "unsupported", type(exc).__name__)


def _fetch_sdk(key: str, cursor: int) -> Mapping[str, Any]:
    path = f"/api/v1/cursor/dispatch/{quote(key, safe='')}/conversation"
    client = make_sync_client(worker_url(), timeout=5.0)
    try:
        response = client.get(path, params={"after": cursor})
        response.raise_for_status()
        body = response.json()
    finally:
        client.close()
    if not isinstance(body, Mapping):
        raise TypeError("sdk_tail_not_object")
    return body


def _fetch_cdp(key: str, cursor: int) -> Mapping[str, Any]:
    from cdp_ask.client import project_ask_base_url

    base = project_ask_base_url()
    if not base:
        raise RuntimeError("project_ask_url_unset")
    payload: dict[str, Any] = {"after_turn": cursor, "limit": 50}
    if "://" in key:
        payload["chat_url"] = key
    else:
        payload["registration_id"] = key
    client = make_sync_client(base, timeout=10.0)
    try:
        response = client.post("/v1/cse-session/harvest", json=payload)
        response.raise_for_status()
        body = response.json()
    finally:
        client.close()
    if not isinstance(body, Mapping):
        raise TypeError("cdp_tail_not_object")
    return body


def _normalize_sdk(body: Mapping[str, Any], cursor: int) -> dict[str, Any]:
    lines = body.get("lines")
    new_cursor = body.get("cursor")
    source = body.get("source")
    return {
        "lines": lines if isinstance(lines, list) else [],
        "cursor": new_cursor if isinstance(new_cursor, int) else cursor,
        "eof": bool(body.get("eof")),
        "source": source if isinstance(source, str) and source else "sdk.run_lines",
    }


def _normalize_cdp(body: Mapping[str, Any], cursor: int) -> dict[str, Any]:
    turns = body.get("turns")
    lines: list[dict[str, Any]] = []
    if isinstance(turns, list):
        for turn in turns:
            if not isinstance(turn, Mapping):
                continue
            text = turn.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            index = turn.get("ordinal")
            author = turn.get("author")
            lines.append(
                {
                    "index": index if isinstance(index, int) else None,
                    "kind": author if isinstance(author, str) and author else "turn",
                    "text": text,
                }
            )
    new_cursor = body.get("cursor")
    if not isinstance(new_cursor, int):
        ordinals = [line["index"] for line in lines if isinstance(line["index"], int)]
        new_cursor = max(ordinals) if ordinals else cursor
    streaming = bool(body.get("streaming")) or body.get("outcome") == "streaming"
    return {
        "lines": lines,
        "cursor": new_cursor,
        "eof": not streaming,
        "source": "cse-dom",
    }
