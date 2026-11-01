"""Durable conversation URL ↔ CDP target binding records (local actuation plane)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from web_tab.config import normalize_cdp_store_key
from web_tab.surface_adapter import canonical_page_url
from web_tab.tab_session import TabSession

_LOCK = threading.Lock()


def default_store_path() -> Path:
    override = os.environ.get("ULG_WEB_TAB_BINDING_STORE", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".gateway" / "web_tab_bindings.json"


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"bindings": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"bindings": {}}
    if not isinstance(raw, dict):
        return {"bindings": {}}
    bindings = raw.get("bindings")
    if not isinstance(bindings, dict):
        return {"bindings": {}}
    return {"bindings": bindings}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def binding_key(*, cdp_url: str, conversation_url: str) -> str:
    return f"{normalize_cdp_store_key(cdp_url)}|{canonical_page_url(conversation_url)}"


def save_binding(session: TabSession, *, path: Path | None = None) -> TabSession:
    store = path or default_store_path()
    key = binding_key(
        cdp_url=session.cdp_url, conversation_url=session.conversation_url
    )
    with _LOCK:
        doc = _read(store)
        doc["bindings"][key] = session.as_dict()
        _write(store, doc)
    return session


def load_binding(
    *,
    cdp_url: str,
    conversation_url: str,
    path: Path | None = None,
) -> TabSession | None:
    store = path or default_store_path()
    key = binding_key(cdp_url=cdp_url, conversation_url=conversation_url)
    with _LOCK:
        row = _read(store)["bindings"].get(key)
    if not isinstance(row, dict):
        return None
    return TabSession(
        target_id=str(row.get("target_id") or ""),
        cdp_url=str(row.get("cdp_url") or cdp_url),
        conversation_url=str(row.get("conversation_url") or conversation_url),
        surface=str(row.get("surface") or "chatgpt"),
        web_socket_debugger_url=(
            str(row["web_socket_debugger_url"])
            if row.get("web_socket_debugger_url")
            else None
        ),
        conversation_id=(
            str(row["conversation_id"]) if row.get("conversation_id") else None
        ),
        created_by_agent=bool(row.get("created_by_agent")),
    )


def delete_binding(
    *,
    cdp_url: str,
    conversation_url: str,
    path: Path | None = None,
) -> bool:
    store = path or default_store_path()
    key = binding_key(cdp_url=cdp_url, conversation_url=conversation_url)
    with _LOCK:
        doc = _read(store)
        if key not in doc["bindings"]:
            return False
        del doc["bindings"][key]
        _write(store, doc)
    return True
