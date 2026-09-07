"""Pure helpers for CDP hop reactor wait/harvest paths (importable for tests)."""

from __future__ import annotations

from typing import Any

# Harvest-miss outcomes that increment the breaker (N4).
HARVEST_MISS_OUTCOMES = frozenset(
    {"unreachable", "not_attached", "conflict", "refused", "http_error"}
)

CDP_EXTERNAL_GATE_LIVE = "cdp_external_gate_live"

# Outcomes that need idle-streak termination when not streaming (AC4 / D2).
PERSISTENT_IDLE_OUTCOMES = frozenset(
    {"no_reply_yet", "incomplete_dom", "unauthenticated", "dormant"}
)


def is_http_error_envelope(value: dict[str, Any] | None) -> bool:
    """True when value is the 4xx/5xx envelope from http_json, not a success body."""
    if not value or not isinstance(value, dict):
        return False
    status = value.get("status")
    return isinstance(status, int) and status >= 400


def http_json_status(
    status_code: int,
    body_text: str,
    *,
    parsed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return envelope {status, body, code} — never collapse 4xx to None."""
    body: dict[str, Any] | str
    if parsed is not None:
        body = parsed
    else:
        body = body_text
    code: str | None = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or "") or None
    return {"status": status_code, "body": body, "code": code}


def is_cdp_external_gate_live(envelope: dict[str, Any] | None) -> bool:
    if not envelope:
        return False
    body = envelope.get("body")
    if not isinstance(body, dict):
        return False
    err = body.get("error")
    if not isinstance(err, dict):
        return False
    return str(err.get("code") or "") == CDP_EXTERNAL_GATE_LIVE


def terminal(
    outcome: str,
    stop: bool,
    streaming: bool,
    tool_pause: bool,
    streak: int,
    *,
    idle_confirmations: int = 2,
) -> bool:
    """Counter-terminated terminal predicate for wait paths."""
    if outcome in HARVEST_MISS_OUTCOMES:
        return streak >= 5
    if stop and not streaming:
        return True
    if tool_pause and streak >= idle_confirmations:
        return True
    if outcome == "harvested" and not streaming and streak >= idle_confirmations:
        return True
    if outcome in PERSISTENT_IDLE_OUTCOMES and not streaming and streak >= idle_confirmations:
        return True
    if not streaming and streak >= idle_confirmations:
        return True
    return False


def id_fields(state: Any) -> dict[str, Any]:
    """Uniform log record for reactor id triple + cursor."""
    return {
        "fable_chat_url": getattr(state, "fable_chat_url", None),
        "fable_stargate_execution_id": getattr(state, "fable_stargate_execution_id", None),
        "fable_satellite_execution_id": getattr(state, "fable_satellite_execution_id", None),
        "fable_registration_id": getattr(state, "fable_registration_id", None),
        "fable_last_turn_ordinal": getattr(state, "fable_last_turn_ordinal", None),
        "fable_thread": getattr(state, "fable_thread", None),
    }


def merge_active_work_rows(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Union execution-store rows and registry seated_rows (identity lives in both)."""
    if not data:
        return []
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("rows", "seated_rows"):
        for row in data.get(key) or []:
            if not isinstance(row, dict):
                continue
            token = str(row.get("registration_id") or row.get("execution_id") or id(row))
            if token in seen:
                continue
            seen.add(token)
            merged.append(row)
    return merged


def build_harvest_request(
    *,
    chat_url: str | None,
    registration_id: str | None,
    satellite_execution_id: str | None,
    stargate_execution_id: str | None,
) -> dict[str, Any]:
    """B1/N1 harvest identity precedence: chat_url ≻ registration ≻ satellite ≻ stargate."""
    body: dict[str, Any] = {}
    if chat_url:
        body["chat_url"] = chat_url
        return body
    if registration_id:
        body["registration_id"] = registration_id
        return body
    if satellite_execution_id:
        body["execution_id"] = satellite_execution_id
        return body
    if stargate_execution_id:
        body["execution_id"] = stargate_execution_id
    return body


def should_drop_satellite_id(harvest: dict[str, Any]) -> bool:
    """N1: drop satellite id after detach so next poll harvests by chat_url."""
    outcome = str(harvest.get("outcome") or "")
    return outcome in {"not_attached", "dormant"}


def harvest_miss_outcome(outcome: str, *, turns: list[Any] | None = None) -> bool:
    if outcome in HARVEST_MISS_OUTCOMES:
        return True
    if outcome in {"no_reply_yet"}:
        return False
    if outcome == "harvested" and not (turns or []):
        return False
    return False


def active_row_absent_streak(
    rows: list[dict[str, Any]] | None,
    fable_thread: str,
    *,
    running_statuses: frozenset[str] = frozenset({"pending", "running"}),
) -> bool:
    """True when no active-work row exists for the lane (M1/N2 — reads `rows`)."""
    if not rows:
        return True
    for row in rows:
        if str(row.get("parent_thread") or "") != fable_thread:
            continue
        status = str(row.get("status") or "")
        if status in running_statuses:
            return False
    return True
