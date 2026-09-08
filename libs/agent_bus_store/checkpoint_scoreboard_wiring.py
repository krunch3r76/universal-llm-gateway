"""CHECKPOINT scoreboard coherence gate (AMEND-R D5)."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from implement_admission.conductor_witness_types import row_status_in_tip


def _record(signal: str, **payload: Any) -> None:
    from agent_bus_store.events.publisher import emit

    emit(signal, payload)

_SCOREBOARD_LINE_RE = re.compile(
    r"^Scoreboard:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_G_CLAIM_RE = re.compile(
    r"\bG(\d+)\s*[:=—\-]?\s*"
    r"(LANDED|DONE|CLEAR|CLOSED|COMPLETE|OPEN|REVISE|BLOCKED|WITHHELD|RETRACTED)\b",
    re.IGNORECASE,
)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DONE_CLASS = frozenset({"LANDED", "DONE", "CLEAR", "CLOSED", "COMPLETE"})
_OPEN_CLASS = frozenset({"OPEN", "REVISE", "BLOCKED", "WITHHELD"})


def _gate_mode() -> str:
    mode = os.environ.get("AGENT_BUS_SCOREBOARD_GATE_MODE", "observe").strip().lower()
    return mode if mode in {"observe", "enforce"} else "observe"


def _status_class(raw: str) -> str:
    token = raw.strip().upper()
    if token in _DONE_CLASS:
        return "DONE"
    if token in _OPEN_CLASS:
        return "OPEN"
    if token == "RETRACTED":
        return "RETRACTED"
    return token


def _claim_class(raw: str) -> str:
    token = raw.strip().upper()
    if token in _DONE_CLASS:
        return "DONE"
    if token in _OPEN_CLASS:
        return "OPEN"
    if token == "RETRACTED":
        return "RETRACTED"
    return token


def _parse_scoreboard_line(body: str) -> str | None:
    match = _SCOREBOARD_LINE_RE.search(body)
    if not match:
        return None
    return match.group(1).strip()


def _parse_g_claims(text: str) -> list[tuple[str, str]]:
    return [
        (f"G{num}", claim)
        for num, claim in _G_CLAIM_RE.findall(text)
    ]


def _resolve_scoreboard_uri(
    scoreboard_line: str,
    *,
    thread: str,
    tags: list[str],
) -> str | None:
    for token in re.split(r"\s*[·|]\s*", scoreboard_line):
        piece = token.strip()
        if piece.startswith("cortex://") and piece.endswith("-scoreboard.md"):
            return piece
        if _SLUG_RE.match(piece):
            return f"cortex://notes/system/scoreboards/{piece}-scoreboard.md"
    for tag in tags:
        if tag.startswith("scoreboard:"):
            slug = tag.removeprefix("scoreboard:").strip()
            if slug:
                return f"cortex://notes/system/scoreboards/{slug}-scoreboard.md"
    return None


def _read_scoreboard_body(uri: str) -> tuple[str, str] | None:
    import os

    if not uri.startswith("cortex://"):
        return None
    root_env = os.environ.get("CORTEX_FILES_ROOT")
    if root_env:
        files_root = Path(root_env)
    else:
        from cortex_store.dispatch_ops._shared import _FILES_ROOT

        files_root = _FILES_ROOT
    path = files_root / uri.removeprefix("cortex://")
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, digest


def assert_scoreboard_coherent(
    *,
    thread: str,
    subject: str,
    body: str,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    """Refuse or annotate CHECKPOINT bodies whose G-row claims drift from scoreboard."""
    from agent_bus_store.checkpoint_projection import is_checkpoint_subject

    if not is_checkpoint_subject(subject):
        return None

    claims = _parse_g_claims(body)
    scoreboard_line = _parse_scoreboard_line(body)
    if claims and scoreboard_line is None:
        from agent_bus_store.thread_classification import classify_thread

        if classify_thread(tags or [])["spine"] == "root":
            _record(
                "agent_bus.checkpoint.scoreboard_line_required",
                thread=thread,
            )
            if _gate_mode() == "enforce":
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "CHECKPOINT prose claims G-row status without Scoreboard: line",
                        "code": "checkpoint.scoreboard_line_required",
                        "reason": "checkpoint.scoreboard_line_required",
                    },
                )
        return None

    if not claims:
        return None

    if not scoreboard_line:
        return None

    uri = _resolve_scoreboard_uri(scoreboard_line, thread=thread, tags=tags or [])
    if uri is None:
        _record("agent_bus.checkpoint.scoreboard_unresolved", thread=thread)
        if _gate_mode() == "enforce":
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "Scoreboard claims present but scoreboard could not be resolved",
                    "code": "checkpoint.scoreboard_unresolved",
                    "reason": "checkpoint.scoreboard_unresolved",
                },
            )
        return None

    loaded = _read_scoreboard_body(uri)
    if loaded is None:
        _record("agent_bus.checkpoint.scoreboard_unresolved", thread=thread, uri=uri)
        if _gate_mode() == "enforce":
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": f"Scoreboard not found at {uri}",
                    "code": "checkpoint.scoreboard_unresolved",
                    "reason": "checkpoint.scoreboard_unresolved",
                },
            )
        return None

    scoreboard_body, scoreboard_sha = loaded
    drift_rows: list[dict[str, str]] = []
    for gid, claim in claims:
        claimed = _claim_class(claim)
        actual_raw = row_status_in_tip(scoreboard_body, gid) or "OPEN"
        actual = _status_class(actual_raw)
        if claimed != actual:
            drift_rows.append({"g": gid, "cp": claim.upper(), "scoreboard": actual_raw})

    if not drift_rows:
        return None

    mode = _gate_mode()
    _record(
        "agent_bus.checkpoint.scoreboard_drift",
        thread=thread,
        mode=mode,
        scoreboard_uri=uri,
        rows=len(drift_rows),
    )
    advisory = {
        "advisory": "checkpoint.scoreboard_drift",
        "scoreboard_uri": uri,
        "scoreboard_sha256": scoreboard_sha,
        "rows": drift_rows,
        "mode": mode,
    }
    if mode == "enforce":
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "CHECKPOINT scoreboard claims drift from resolved scoreboard",
                "code": "checkpoint.scoreboard_drift",
                "reason": "checkpoint.scoreboard_drift",
                "scoreboard_uri": uri,
                "scoreboard_sha256": scoreboard_sha,
                "rows": drift_rows,
            },
        )
    return advisory


__all__ = ["assert_scoreboard_coherent"]
