"""Click-time prompt read. Nothing returned here enters the fold.

Cursor-sdk text is the admit record: the packet file when one was stored,
otherwise the message. CDP text is the staged ``prompt.md`` for that
execution, when the generate path wrote one.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

_CAP = 48_000
_KEY_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")
_CDP_REL = Path("notes/system/ephemeral/cdp-endpoint")


def fetch_prompt(key: str) -> dict[str, Any]:
    """Return the sent prompt for ``key``. Never raises."""
    try:
        if not _safe_key(key):
            return _shape(key, "", "prompt", error="bad_key")
        sdk = _sdk_prompt(key)
        if sdk is not None:
            return sdk
        cdp = _cdp_prompt(key)
        if cdp is not None:
            return cdp
        return _shape(key, "", "prompt", error="not_found")
    except Exception as exc:  # noqa: BLE001 — the pane prints the error
        return _shape(key, "", "prompt", error=type(exc).__name__)


def _safe_key(key: str) -> bool:
    return bool(key) and len(key) <= 128 and all(ch in _KEY_OK for ch in key)


def _shape(
    key: str,
    body: str,
    source: str,
    *,
    error: str = "",
) -> dict[str, Any]:
    shown, truncated = _cap(body)
    return {
        "kind": "prompt",
        "key": key,
        "source": source,
        "body": shown,
        "bytes": len(body.encode("utf-8")),
        "truncated": truncated,
        "error": error,
    }


def _cap(body: str) -> tuple[str, bool]:
    if len(body) <= _CAP:
        return body, False
    return body[:_CAP], True


def _ledger_path() -> Path:
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    return data_dir / "cursor-sdk-dispatch.db"


def _sdk_prompt(key: str) -> dict[str, Any] | None:
    path = _ledger_path()
    if not path.is_file():
        return None
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        row = conn.execute(
            "SELECT record_json, packet_path FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    message, packet = _record_fields(row[0], row[1])
    if packet:
        try:
            text = _read_packet(packet)
        except OSError:
            text = ""
        if text:
            return _shape(key, text, "sdk.packet")
    if message:
        return _shape(key, message, "sdk.message")
    return _shape(key, "", "sdk.message", error="empty_prompt")


def _record_fields(record_json: str | None, column_packet: str | None) -> tuple[str, str]:
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    message = data.get("message")
    packet = data.get("packet_path") or column_packet
    return (
        message if isinstance(message, str) else "",
        packet.strip() if isinstance(packet, str) else "",
    )


def _read_packet(raw: str) -> str:
    if raw.startswith("workspaces://"):
        rest = raw[len("workspaces://") :]
        _, _, rel = rest.partition("/")
        root = Path(os.environ.get("PROJECT_ROOT") or "/mnt/torus/projects")
        return (root / rel).read_text(encoding="utf-8")
    path = Path(raw)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    root = Path(os.environ.get("PROJECT_ROOT") or "/mnt/torus/projects")
    return (root / raw).read_text(encoding="utf-8")


def _cdp_prompt(key: str) -> dict[str, Any] | None:
    root = Path(os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files"))
    path = (root / _CDP_REL / key / "prompt.md").resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return _shape(key, path.read_text(encoding="utf-8"), "cse.prompt_md")
