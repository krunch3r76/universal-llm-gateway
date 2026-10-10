"""Load and bind the sealed handoff letter referenced by a CHECKPOINT tip."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from continuity_tape.handoff import (
    HANDOFF_MAX_BYTES,
    handoff_binding,
    handoff_binding_stamped,
    handoff_json_size,
    handoff_lag_turns,
    handoff_sha12,
)
from implement_admission.closeout_helpers import cortex_files_root

_HARVEST_REFUSED_RE = re.compile(r"Harvest:\s*refused\(", re.IGNORECASE)
_HARVEST_TRANSCRIPT_RE = re.compile(
    r"Harvest:\s*transcript:([^\s·]+)",
    re.IGNORECASE,
)
_LETTER_SHA12_RE = re.compile(
    r"Letter:\s*handoff:(?P<sha>[0-9a-fA-F]{12}|none)",
    re.IGNORECASE,
)
_HOUSE_CP_PARTS_RE = re.compile(r"^house-cp:(.+):([1-9][0-9]*)$")
_VALID_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _missing(reason: str, **fields: Any) -> dict[str, Any]:
    return {
        "binding": "missing",
        "reasons": [reason],
        "sha12": None,
        "session_id": fields.get("session_id"),
        "seal_uri": fields.get("seal_uri"),
        "lag_turns": None,
        "messages_behind": None,
        "body": None,
    }


def _parse_sid(tip_body: str) -> str | None:
    text = tip_body or ""
    if _HARVEST_REFUSED_RE.search(text):
        return None
    match = _HARVEST_TRANSCRIPT_RE.search(text)
    if not match:
        return None
    sid = match.group(1).strip()
    return sid or None


def _parse_tip_sha12(tip_body: str) -> str | None:
    match = _LETTER_SHA12_RE.search(tip_body or "")
    if not match:
        return None
    token = match.group("sha").lower()
    if token == "none":
        return None
    return token


def _seals_root() -> Path:
    return (cortex_files_root() / "notes/system/seals").resolve()


def _sid_allowed(sid: str) -> bool:
    if ".." in sid:
        return False
    return _VALID_SID_RE.fullmatch(sid) is not None


def _seal_path(sid: str) -> Path | None:
    if not _sid_allowed(sid):
        return None
    root = _seals_root()
    path = (root / f"{sid}.messages.json").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _seal_uri(sid: str) -> str:
    return f"cortex://notes/system/seals/{sid}.messages.json"


def _messages_behind(
    messages: list[Any] | None,
    letter: dict[str, Any],
) -> int | None:
    if messages is None:
        return None
    index = letter.get("as_of_message_index")
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    return len(messages) - index


def load_tip_letter(tip_body: str) -> dict[str, Any]:
    """Resolve the sealed handoff letter for *tip_body*; never raises."""
    sid = _parse_sid(tip_body)
    if sid is None:
        return _missing("no_seal")

    seal_uri = _seal_uri(sid)
    path = _seal_path(sid)
    if path is None:
        return _missing("bad_sid", session_id=sid, seal_uri=seal_uri)
    try:
        if not path.is_file():
            return _missing("no_file", session_id=sid, seal_uri=seal_uri)
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _missing("unreadable", session_id=sid, seal_uri=seal_uri)

    if not isinstance(data, dict):
        return _missing("unreadable", session_id=sid, seal_uri=seal_uri)

    handoff = data.get("handoff")
    if handoff is None:
        return _missing("no_letter", session_id=sid, seal_uri=seal_uri)

    if not isinstance(handoff, dict):
        return {
            "binding": "stale",
            "reasons": ["shape"],
            "sha12": None,
            "session_id": sid,
            "seal_uri": seal_uri,
            "lag_turns": None,
            "messages_behind": None,
            "body": None,
        }

    messages = data.get("messages")
    if not isinstance(messages, list):
        messages = []

    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    messages_sha256 = str(meta.get("messages_sha256") or "")
    session_id = str(meta.get("session_id") or sid)

    if isinstance(handoff.get("house_cp"), str):
        parts = _HOUSE_CP_PARTS_RE.match(handoff["house_cp"])
        if parts:
            house, n_text = parts.group(1), parts.group(2)
            binding, reasons = handoff_binding(
                handoff,
                messages=messages,
                messages_sha256=messages_sha256,
                house=house,
                n=int(n_text),
            )
        else:
            binding, reasons = "stale", ["house_cp"]
    else:
        binding, reasons = handoff_binding_stamped(
            handoff,
            messages=messages,
            messages_sha256=messages_sha256,
            session_id=session_id,
        )

    reasons = list(reasons)
    tip_sha12 = _parse_tip_sha12(tip_body)
    letter_sha12 = handoff_sha12(handoff)
    sha12 = None if letter_sha12 == "none" else letter_sha12
    if tip_sha12 is not None and tip_sha12 != letter_sha12:
        if "tip_sha12" not in reasons:
            reasons.append("tip_sha12")
        binding = "stale"

    body: dict[str, Any] | None = handoff
    if handoff_json_size(handoff) > HANDOFF_MAX_BYTES:
        body = None
        if "too_large" not in reasons:
            reasons.append("too_large")

    return {
        "binding": binding,
        "reasons": reasons,
        "sha12": sha12,
        "session_id": sid,
        "seal_uri": seal_uri,
        "lag_turns": handoff_lag_turns(handoff),
        "messages_behind": _messages_behind(messages, handoff),
        "body": body,
    }


__all__ = ["load_tip_letter"]
