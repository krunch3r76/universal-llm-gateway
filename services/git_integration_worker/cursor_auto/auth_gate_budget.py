"""Auth-gate failure budget — classify + count before nested SDK admit.

Caps repeated cursor-auto re-dispatch spend after auth-class CLOSEOUTs
(friction 26462 / 5978-class). Composed into ``admit_gates.blocking_admit_gate``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_META_LINE_RE = re.compile(r"(?im)^meta:\s*(.+)$")
_STATUS_LINE_RE = re.compile(r"(?im)^status:\s*(\S+)")
_ACK_RE = re.compile(r"(?im)^auth_gate_ack\s*[:=]\s*(\S+)")
# Index-agnostic + verdict guard (rev 4) — fail/not_tested with auth token on same line.
_RULE1_AC_RE = re.compile(
    r"(?i)ac_verdict:\s*AC\d+\s*=\s*(fail|not_tested)\b"
    r"[^\n]*(blocked_auth|sign.?in|logged.?out|session)"
)
_AUTH_TOKEN_RE = re.compile(
    r"(?i)(SIGN IN|sign-in|logged-out|blocked_auth)"
)
_PASSWORD_OVERLAY_RE = re.compile(
    r"(?i)password field.*(?:overlay|null|empty|blocked)"
    r"|(?:overlay|null|empty|blocked).*password field"
)

AUTH_GATE_BUDGET_ENABLED = os.environ.get(
    "AUTH_GATE_BUDGET_ENABLED", "true"
).lower() not in {"0", "false", "no", "off"}
AUTH_GATE_BUDGET = int(os.environ.get("AUTH_GATE_BUDGET", "2"))
AUTH_GATE_BUDGET_POST_ACK = int(os.environ.get("AUTH_GATE_BUDGET_POST_ACK", "1"))
AUTH_GATE_BUDGET_THREAD_SCOPED = os.environ.get(
    "AUTH_GATE_BUDGET_THREAD_SCOPED", "true"
).lower() not in {"0", "false", "no", "off"}


def _turn_number(turn: dict[str, Any]) -> int:
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def _meta_from_body(body: str) -> dict[str, Any]:
    match = _META_LINE_RE.search(body or "")
    if match is None:
        return {}
    try:
        meta = json.loads(match.group(1).strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _closeout_status(body: str) -> str:
    match = _STATUS_LINE_RE.search(body or "")
    if match is None:
        return ""
    return match.group(1).strip().lower()


def _is_cursor_auto_closeout(turn: dict[str, Any]) -> bool:
    if turn.get("from") != "cursor-auto":
        return False
    body = str(turn.get("body") or "")
    return "TYPE: CLOSEOUT" in body


def _contract_from_turn(turn: dict[str, Any], body: str) -> str | None:
    meta = _meta_from_body(body)
    raw = meta.get("contract")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    # Preceding DIRECTIVE on same thread may carry contract: — caller can
    # stamp meta.contract at relay; historical turns often omit it.
    m = re.search(r"(?im)^contract:\s*(\S+)", body)
    if m:
        return m.group(1).strip().lower()
    return None


def payload_has_rule1_auth_ac(payload: str) -> bool:
    """True when resolved closeout payload matches Rule 1 (tagger input)."""
    return _RULE1_AC_RE.search(payload or "") is not None


def classify_auth_gate(turn: dict[str, Any]) -> bool:
    """Return True when a relayed CLOSEOUT counts as an auth-gate failure."""
    if not _is_cursor_auto_closeout(turn):
        return False
    body = str(turn.get("body") or "")
    contract = _contract_from_turn(turn, body)
    if contract == "confer":
        return False

    meta = _meta_from_body(body)
    if meta.get("gate_class") == "auth_gate":
        return True

    # Rule 1 — structured ac_verdict (status-independent).
    if payload_has_rule1_auth_ac(body):
        return True

    # Rule 2 — substring fallback for synthesized / unauthored envelopes.
    status = _closeout_status(body)
    if status not in {"blocked", "partial"}:
        return False
    if _AUTH_TOKEN_RE.search(body):
        return True
    if _PASSWORD_OVERLAY_RE.search(body):
        return True
    return False


def parse_auth_gate_ack(body: str) -> str | None:
    """Return thread_id or dispatch_id from an operator ``auth_gate_ack:`` line."""
    match = _ACK_RE.search(body or "")
    if match is None:
        return None
    return match.group(1).strip()


def _last_valid_ack_turn(
    turns: list[dict[str, Any]],
    *,
    operator_from: str,
) -> int:
    ordered = sorted(turns, key=_turn_number)
    last_ack_turn = -1
    for turn in ordered:
        if turn.get("from") != operator_from:
            continue
        if parse_auth_gate_ack(str(turn.get("body") or "")):
            last_ack_turn = _turn_number(turn)
    return last_ack_turn


def count_auth_gate_failures(
    turns: list[dict[str, Any]],
    *,
    operator_from: str,
) -> int:
    """Count classified auth-gate CLOSEOUTs since the latest matching ack."""
    last_ack_turn = _last_valid_ack_turn(turns, operator_from=operator_from)
    count = 0
    for turn in sorted(turns, key=_turn_number):
        if _turn_number(turn) <= last_ack_turn:
            continue
        if classify_auth_gate(turn):
            count += 1
    return count


def effective_auth_gate_budget(
    turns: list[dict[str, Any]],
    *,
    operator_from: str,
) -> tuple[int, bool]:
    """Return ``(limit, post_ack)`` for the active counting window."""
    post_ack = _last_valid_ack_turn(turns, operator_from=operator_from) >= 0
    limit = AUTH_GATE_BUDGET_POST_ACK if post_ack else AUTH_GATE_BUDGET
    return limit, post_ack


def pending_auth_gate_block(
    turns: list[dict[str, Any]],
    *,
    operator_from: str,
    budget: int | None = None,
) -> bool:
    """True when classified failures since last ack reach the effective budget."""
    if not AUTH_GATE_BUDGET_ENABLED:
        return False
    if not AUTH_GATE_BUDGET_THREAD_SCOPED:
        return False
    limit, _ = effective_auth_gate_budget(turns, operator_from=operator_from)
    if budget is not None:
        limit = budget
    return count_auth_gate_failures(turns, operator_from=operator_from) >= limit


def tag_gate_class_for_payload(payload: str) -> str | None:
    """Return ``auth_gate`` when Rule 1 matches the resolved closeout payload."""
    if payload_has_rule1_auth_ac(payload):
        return "auth_gate"
    return None


__all__ = [
    "AUTH_GATE_BUDGET",
    "AUTH_GATE_BUDGET_ENABLED",
    "AUTH_GATE_BUDGET_POST_ACK",
    "AUTH_GATE_BUDGET_THREAD_SCOPED",
    "classify_auth_gate",
    "count_auth_gate_failures",
    "effective_auth_gate_budget",
    "parse_auth_gate_ack",
    "payload_has_rule1_auth_ac",
    "pending_auth_gate_block",
    "tag_gate_class_for_payload",
]
