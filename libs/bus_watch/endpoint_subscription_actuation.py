"""S2 prepare NOTE and hop REQUEST actuation for endpoint subscription holders."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bus_watch.endpoint_subscription_row_parse import is_uuid_agent_id
from bus_watch.fable_lock import WATCH_DIR

_CANONICAL_REQUEST_SUBJECT = "REQUEST| precompaction continuity"
_FORBIDDEN_SUBJECT_TOKENS = ("checkpoint", "hop-", "admitted", "progress")
_POLICY_KEY = "endpoint_subscription_hop_actuation"
_REQUEST_KEY_PREFIX = "hop-actuation-key:"

CANONICAL_REQUEST_SUBJECT = _CANONICAL_REQUEST_SUBJECT


def hop_actuation_policy_enabled(policy: dict[str, Any]) -> bool:
    return policy.get(_POLICY_KEY) is True


def brg_actuation_allowed(scope: str, policy: dict[str, Any]) -> bool:
    if str(scope) != "24600":
        return hop_actuation_policy_enabled(policy)
    return (
        hop_actuation_policy_enabled(policy)
        and policy.get("endpoint_subscription_hop_actuation_brg") is True
    )


def request_subject_is_clean(subject: str) -> bool:
    lowered = (subject or "").lower()
    return not any(tok in lowered for tok in _FORBIDDEN_SUBJECT_TOKENS)


def build_note_subject() -> str:
    return "NOTE| prepare advisory"


def build_request_subject() -> str:
    return _CANONICAL_REQUEST_SUBJECT


def hop_actuation_key(scope: str, holder_uuid: str, band: str) -> str:
    return f"{_REQUEST_KEY_PREFIX} N={scope} U={holder_uuid} band={band}"


def prepare_note_key(scope: str, holder_uuid: str) -> str:
    return f"prepare-note: N={scope} U={holder_uuid}"


def parse_hop_request_uuid(body: str) -> str | None:
    match = re.search(r"U=([0-9a-f-]{36})", body or "", re.I)
    return match.group(1).lower() if match else None


def actuation_record_path(scope: str) -> Path:
    slug = re.sub(r"[^0-9a-zA-Z_-]+", "_", str(scope))
    return WATCH_DIR / f"liaison-hop-actuation-{slug}.json"


def _load_actuation_record(scope: str) -> dict[str, Any]:
    path = actuation_record_path(scope)
    if not path.is_file():
        return {"keys": [], "hop_requests": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"keys": [], "hop_requests": {}}
    if not isinstance(data, dict):
        return {"keys": [], "hop_requests": {}}
    keys = data.get("keys")
    if not isinstance(keys, list):
        keys = []
    hop_requests = data.get("hop_requests")
    if not isinstance(hop_requests, dict):
        hop_requests = {}
    return {"keys": [str(k) for k in keys], "hop_requests": hop_requests}


def _save_actuation_record(scope: str, state: dict[str, Any]) -> None:
    path = actuation_record_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _record_actuation_key(
    scope: str,
    key: str,
    *,
    hop_request_meta: dict[str, Any] | None = None,
) -> None:
    state = _load_actuation_record(scope)
    keys = list(state.get("keys") or [])
    if key not in keys:
        keys.append(key)
    state["keys"] = keys
    if hop_request_meta is not None:
        hop_requests = dict(state.get("hop_requests") or {})
        hop_requests[key] = hop_request_meta
        state["hop_requests"] = hop_requests
    _save_actuation_record(scope, state)


def actuation_key_seen(scope: str, key: str) -> bool:
    return key in (_load_actuation_record(scope).get("keys") or [])


def load_hop_request_meta(scope: str, holder_uuid: str, band: str) -> dict[str, Any]:
    key = hop_actuation_key(scope, holder_uuid, band)
    rec = _load_actuation_record(scope)
    meta = (rec.get("hop_requests") or {}).get(key)
    return dict(meta) if isinstance(meta, dict) else {}


def build_request_body(
    *,
    scope: str,
    holder_uuid: str,
    band: str,
    as_of: str,
    used_tokens: int,
    window_limit_tokens: int,
) -> str:
    key = hop_actuation_key(scope, holder_uuid, band)
    return (
        f"{key}\n"
        f"scope={scope} holder={holder_uuid} band={band} as_of={as_of} "
        f"used={used_tokens} limit={window_limit_tokens}\n"
        "Order: reconcile receipts → CHECKPOINT → spawn successor → "
        "successor ACK on scope → re-subscribe and move claim.\n"
        "No hop protocol ⇒ reply BLOCKED|"
    )


def maybe_post_prepare_note(
    store: Any,
    *,
    scope: str,
    holder_row: dict[str, Any],
    prepare_advisory: dict[str, Any] | None,
    mailbox_agent: str | None = None,
) -> bool:
    holder_uuid = is_uuid_agent_id(holder_row.get("agent_id"))
    if not holder_uuid:
        return False
    key = prepare_note_key(scope, holder_uuid)
    if actuation_key_seen(scope, key):
        return False
    agent = mailbox_agent or str(holder_row.get("agent") or "").strip()
    if not agent:
        return False
    body = json.dumps(prepare_advisory or {}, indent=2)
    store.create_turn(
        thread=scope,
        from_agent="orion",
        to_agent=agent,
        subject=build_note_subject(),
        body=body,
    )
    _record_actuation_key(scope, key)
    return True


def maybe_post_hop_request(
    store: Any,
    *,
    scope: str,
    holder_row: dict[str, Any],
    band: str,
    policy_enabled: bool,
    glass_generating: bool,
    as_of: str,
    used_tokens: int,
    window_limit_tokens: int,
    mailbox_agent: str | None = None,
) -> dict[str, Any] | None:
    if not policy_enabled:
        return None
    if glass_generating:
        return None
    if band not in ("hop", "emergency"):
        return None
    holder_uuid = is_uuid_agent_id(holder_row.get("agent_id"))
    if not holder_uuid:
        return None
    key = hop_actuation_key(scope, holder_uuid, band)
    if actuation_key_seen(scope, key):
        return None
    agent = mailbox_agent or str(holder_row.get("agent") or "").strip()
    if not agent:
        return None
    subject = build_request_subject()
    turn = store.create_turn(
        thread=scope,
        from_agent="orion",
        to_agent=agent,
        subject=subject,
        body=build_request_body(
            scope=scope,
            holder_uuid=holder_uuid,
            band=band,
            as_of=as_of,
            used_tokens=used_tokens,
            window_limit_tokens=window_limit_tokens,
        ),
    )
    turn_id = None
    turn_number = None
    if isinstance(turn, dict):
        nested = turn.get("turn")
        if isinstance(nested, dict):
            turn_id = nested.get("id")
            turn_number = nested.get("turn_number")
        else:
            turn_id = turn.get("id")
            turn_number = turn.get("turn_number")
    _record_actuation_key(
        scope,
        key,
        hop_request_meta={
            "holder_uuid": holder_uuid,
            "subscription_id": holder_row.get("id"),
            "turn_id": turn_id,
            "turn_number": turn_number,
            "mailbox_agent": agent,
        },
    )
    return turn if isinstance(turn, dict) else {"turn": turn}


def wake_ledger_delivered_for_turn(
    store: Any,
    *,
    scope: str,
    turn_id: int | None,
) -> bool:
    if turn_id is None:
        return False
    try:
        payload = store._bus(
            "GET",
            "/endpoint-wake/ledger",
            params={"thread": scope, "turn_id": turn_id, "limit": 10},
        )
    except Exception:
        return False
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = str(row.get("state") or "")
        if state == "delivered":
            return True
        if state in ("deferred", "pending") and row.get("delivered_at"):
            return True
    return False


def pressure_band_allows_request(band: str | None) -> bool:
    if band == "emergency":
        return True
    return band == "hop"


__all__ = [
    "CANONICAL_REQUEST_SUBJECT",
    "actuation_key_seen",
    "brg_actuation_allowed",
    "build_note_subject",
    "build_request_body",
    "build_request_subject",
    "hop_actuation_key",
    "hop_actuation_policy_enabled",
    "load_hop_request_meta",
    "maybe_post_hop_request",
    "maybe_post_prepare_note",
    "parse_hop_request_uuid",
    "prepare_note_key",
    "pressure_band_allows_request",
    "request_subject_is_clean",
    "wake_ledger_delivered_for_turn",
]
