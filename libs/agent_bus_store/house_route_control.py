"""ROUTE/CONTROL envelope validation and gc1 ruling gate on house-thread sends."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from agent_seat.registry import STAGE_MANAGER_ADDRESSES
from pydantic import BaseModel, Field, ValidationError

_GC1 = "grok-bot-gc1"
_ROUTE_CONTROL_TYPE = "TYPE: ROUTE/CONTROL"
_LIFECYCLE_KINDS = frozenset({"COMMISSION", "DECOMMISSION", "WAKE"})
_RULING_KINDS = frozenset({"ACK", "RULING", "AMEND", "PARK", "CANCEL"})
_CONTROL_KINDS = _LIFECYCLE_KINDS | _RULING_KINDS
_AUTHORITY_VALUES = frozenset({"operator", "supervisor", "house"})
_SCHEMA_PATH = Path(__file__).with_name("route_control_schema.json")


class RouteControlEnvelope(BaseModel):
    """Parsed ROUTE/CONTROL fields for a house control turn."""

    from_agent: str = Field(alias="from")
    authority: Literal["operator", "supervisor", "house"]
    kind: Literal[
        "COMMISSION",
        "DECOMMISSION",
        "WAKE",
        "ACK",
        "RULING",
        "AMEND",
        "PARK",
        "CANCEL",
    ]
    work_key: str
    read_shas: str

    model_config = {"populate_by_name": True}


def load_route_control_json_schema() -> dict[str, Any]:
    """Return the JSON Schema document for the envelope."""
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _first_non_empty_line(text: str) -> str | None:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _line_value(body: str, key: str) -> str | None:
    prefix = key.lower() + ":"
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            _, _, rest = stripped.partition(":")
            value = rest.strip()
            return value if value else None
    return None


def _subject_head_kind(subject: str) -> str | None:
    head = (subject or "").split("|", 1)[0].strip().upper()
    if head in _CONTROL_KINDS:
        return head
    return None


def _subject_work_key(subject: str) -> str | None:
    parts = [p.strip() for p in (subject or "").split("|") if p.strip()]
    if len(parts) < 2:
        return None
    candidate = parts[1]
    if candidate.upper() in _CONTROL_KINDS:
        return None
    return candidate


def is_house_thread(tags: list[str] | None) -> bool:
    """True for continuity roots and Maestro Sidekick lanes."""
    for tag in tags or []:
        if tag == "role:root" or tag.startswith("sidekick:"):
            return True
    return False


def requires_route_control_envelope(
    *,
    tags: list[str] | None,
    subject: str,
    body: str,
) -> bool:
    """House control turns carry ROUTE/CONTROL fields."""
    if not is_house_thread(tags):
        return False
    if _subject_head_kind(subject):
        return True
    first = _first_non_empty_line(body)
    return first == _ROUTE_CONTROL_TYPE


def parse_route_control_envelope(
    *,
    from_agent: str,
    subject: str,
    body: str,
) -> tuple[RouteControlEnvelope | None, list[str]]:
    """Parse envelope fields; return (model, missing_field_names)."""
    missing: list[str] = []
    kind = _line_value(body, "kind") or _subject_head_kind(subject)
    if not kind:
        missing.append("kind")
    else:
        kind = kind.upper()
        if kind not in _CONTROL_KINDS:
            missing.append("kind")

    authority_raw = _line_value(body, "authority")
    if not authority_raw:
        missing.append("authority")
    elif authority_raw.strip().lower() not in _AUTHORITY_VALUES:
        missing.append("authority")

    work_key = _line_value(body, "work_key") or _subject_work_key(subject)
    if not work_key:
        missing.append("work_key")

    read_shas = _line_value(body, "read_shas")
    if not read_shas:
        missing.append("read_shas")

    if not from_agent:
        missing.append("from")

    if missing:
        return None, missing

    assert authority_raw is not None
    assert kind is not None
    assert work_key is not None
    assert read_shas is not None

    try:
        envelope = RouteControlEnvelope.model_validate(
            {
                "from": from_agent,
                "authority": authority_raw.strip().lower(),
                "kind": kind,
                "work_key": work_key,
                "read_shas": read_shas,
            }
        )
    except ValidationError:
        return None, ["schema"]
    return envelope, []


def envelope_enforce_mode() -> Literal["off", "on"]:
    raw = os.environ.get("AGENT_BUS_ENVELOPE_ENFORCE", "off").strip().lower()
    return "on" if raw == "on" else "off"


def ruling_gate_mode() -> Literal["bypass", "on"]:
    raw = os.environ.get("AGENT_BUS_RULING_GATE", "bypass").strip().lower()
    return "on" if raw == "on" else "bypass"


def gc1_acting_maestro_fact_set() -> bool:
    """Clearable config: #49 acting-Maestro allowlist (Kaywan 11:07 PT Oct 9)."""
    raw = os.environ.get("AGENT_BUS_GC1_ACTING_MAESTRO", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def drift_d7d8_mode() -> Literal["log-only", "off"]:
    """Observability-only drift checks (never block send)."""
    raw = os.environ.get("AGENT_BUS_DRIFT_D7D8", "log-only").strip().lower()
    return "off" if raw == "off" else "log-only"


_d7_control_total = 0
_d7_bypass_count = 0
_d8_misreply_count = 0


def _reset_drift_counters_for_tests() -> None:
    """Test isolation — not for production use."""
    global _d7_control_total, _d7_bypass_count, _d8_misreply_count
    _d7_control_total = 0
    _d7_bypass_count = 0
    _d8_misreply_count = 0


def route_control_drift_summary() -> dict[str, float | int]:
    """In-process D7/D8 counters for observability and tests."""
    rate = (
        float(_d7_bypass_count) / float(_d7_control_total)
        if _d7_control_total
        else 0.0
    )
    return {
        "d7_control_total": _d7_control_total,
        "d7_bypass_count": _d7_bypass_count,
        "d7_bypass_rate": rate,
        "d8_misreply_count": _d8_misreply_count,
    }


def would_envelope_reject_on_enforce(
    *,
    from_agent: str,
    subject: str,
    body: str,
    tags: list[str] | None,
) -> bool:
    """True when ENVELOPE_ENFORCE=on would reject this house control turn."""
    if not requires_route_control_envelope(tags=tags, subject=subject, body=body):
        return False
    envelope, _missing = parse_route_control_envelope(
        from_agent=from_agent,
        subject=subject,
        body=body,
    )
    return envelope is None


def would_ruling_gate_refuse_if_on(
    *,
    from_agent: str,
    subject: str,
    body: str,
    tags: list[str] | None,
) -> bool:
    """True when RULING_GATE=on would refuse this house control turn."""
    if not is_house_thread(tags):
        return False

    envelope, _missing = parse_route_control_envelope(
        from_agent=from_agent,
        subject=subject,
        body=body,
    )
    kind = envelope.kind.upper() if envelope else (_subject_head_kind(subject) or "")
    if not kind:
        return False

    if kind in _LIFECYCLE_KINDS:
        return from_agent not in STAGE_MANAGER_ADDRESSES

    if kind not in _RULING_KINDS or from_agent not in STAGE_MANAGER_ADDRESSES:
        return False

    authority = (
        envelope.authority if envelope else _line_value(body, "authority") or ""
    ).lower()
    return not (gc1_acting_maestro_fact_set() and authority == "operator")


def observe_drift_d7_bypass(
    *,
    thread_id: str,
    from_agent: str,
    subject: str,
    body: str,
    tags: list[str] | None,
) -> None:
    """LOG-ONLY: count bypassed enforce/gate for house control turns that posted."""
    global _d7_control_total, _d7_bypass_count
    if drift_d7d8_mode() == "off":
        return
    if not requires_route_control_envelope(tags=tags, subject=subject, body=body):
        return

    _d7_control_total += 1
    bypass = False
    reasons: list[str] = []
    if (
        envelope_enforce_mode() == "off"
        and would_envelope_reject_on_enforce(
            from_agent=from_agent,
            subject=subject,
            body=body,
            tags=tags,
        )
    ):
        bypass = True
        reasons.append("envelope_enforce_off")
    if (
        ruling_gate_mode() == "bypass"
        and would_ruling_gate_refuse_if_on(
            from_agent=from_agent,
            subject=subject,
            body=body,
            tags=tags,
        )
    ):
        bypass = True
        reasons.append("ruling_gate_bypass")

    if bypass:
        _d7_bypass_count += 1

    summary = route_control_drift_summary()
    _emit(
        "agent_bus.route_control.drift_d7_observed",
        thread=thread_id,
        bypass=bypass,
        bypass_reasons=reasons,
        **summary,
    )
    if bypass:
        _emit(
            "agent_bus.route_control.drift_d7_bypass",
            thread=thread_id,
            bypass_reasons=reasons,
            **summary,
        )


def observe_drift_d8_reply_path(
    *,
    thread_id: str,
    from_agent: str,
    to_agent: str,
    tags: list[str] | None,
    prior_turn: dict[str, Any] | None,
) -> None:
    """LOG-ONLY: house reply whose to differs from the requesting control turn's from."""
    global _d8_misreply_count
    if drift_d7d8_mode() == "off":
        return
    if not prior_turn or not is_house_thread(tags):
        return

    prior_from = str(prior_turn.get("from_agent") or "")
    prior_to = str(prior_turn.get("to_agent") or "")
    prior_subject = str(prior_turn.get("subject") or "")
    prior_body = str(prior_turn.get("body") or "")
    if not requires_route_control_envelope(
        tags=tags,
        subject=prior_subject,
        body=prior_body,
    ):
        return
    if prior_to != from_agent:
        return
    if to_agent == prior_from:
        return

    _d8_misreply_count += 1
    _emit(
        "agent_bus.route_control.drift_d8_reply_path",
        thread=thread_id,
        from_agent=from_agent,
        to_agent=to_agent,
        expected_to=prior_from,
        prior_turn_number=prior_turn.get("turn_number"),
        **route_control_drift_summary(),
    )


def _emit(signal: str, **payload: Any) -> None:
    from agent_bus_store.events.publisher import emit

    emit(signal, payload)


def validate_route_control_envelope(
    *,
    thread_id: str,
    from_agent: str,
    subject: str,
    body: str,
    tags: list[str] | None,
) -> dict[str, Any] | None:
    """Log or refuse malformed ROUTE/CONTROL envelopes on house turns."""
    if not requires_route_control_envelope(tags=tags, subject=subject, body=body):
        return None

    envelope, missing = parse_route_control_envelope(
        from_agent=from_agent,
        subject=subject,
        body=body,
    )
    if envelope is not None:
        return None

    detail: dict[str, Any] = {
        "error": "ROUTE/CONTROL envelope missing or invalid required fields",
        "code": "route_control.malformed",
        "reason": "REJECT|MALFORMED",
        "missing_fields": missing,
        "thread": thread_id,
    }
    mode = envelope_enforce_mode()
    _emit(
        "agent_bus.route_control.envelope_malformed",
        thread=thread_id,
        mode=mode,
        missing=missing,
        from_agent=from_agent,
        subject=subject,
    )
    if mode == "on":
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )
    return {"advisory": detail["code"], "mode": "off", **detail}


def apply_ruling_gate(
    *,
    thread_id: str,
    from_agent: str,
    subject: str,
    body: str,
    tags: list[str] | None,
) -> dict[str, Any] | None:
    """Lifecycle from gc1 only; gc1 work rulings need acting-Maestro fact + operator authority."""
    if ruling_gate_mode() == "bypass":
        return None
    if not is_house_thread(tags):
        return None

    envelope, _missing = parse_route_control_envelope(
        from_agent=from_agent,
        subject=subject,
        body=body,
    )
    kind = envelope.kind.upper() if envelope else (_subject_head_kind(subject) or "")
    if not kind:
        return None

    if kind in _LIFECYCLE_KINDS:
        if from_agent not in STAGE_MANAGER_ADDRESSES:
            detail = {
                "error": f"lifecycle kind {kind} accepted only from Stage Manager seats",
                "code": "route_control.lifecycle_from_gc1_only",
                "reason": "REJECT",
                "kind": kind,
                "from": from_agent,
                "thread": thread_id,
            }
            _emit("agent_bus.route_control.lifecycle_refused", **detail)
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=detail,
            )
        return None

    if kind not in _RULING_KINDS:
        return None

    if from_agent not in STAGE_MANAGER_ADDRESSES:
        return None

    authority = (envelope.authority if envelope else _line_value(body, "authority") or "").lower()
    if gc1_acting_maestro_fact_set() and authority == "operator":
        return None

    detail = {
        "error": (
            f"work ruling {kind} from Stage Manager refused without acting-Maestro fact "
            "stamping authority=operator"
        ),
        "code": "route_control.gc1_ruling_refused",
        "reason": "REJECT",
        "kind": kind,
        "from": from_agent,
        "authority": authority or None,
        "acting_maestro_fact": gc1_acting_maestro_fact_set(),
        "thread": thread_id,
    }
    _emit("agent_bus.route_control.gc1_ruling_refused", **detail)
    from fastapi import HTTPException, status

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=detail,
    )


def assert_house_route_control(
    *,
    thread_id: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    tags: list[str] | None,
    prior_turn: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Envelope validation then ruling gate (send-path entry)."""
    advisory = validate_route_control_envelope(
        thread_id=thread_id,
        from_agent=from_agent,
        subject=subject,
        body=body,
        tags=tags,
    )
    apply_ruling_gate(
        thread_id=thread_id,
        from_agent=from_agent,
        subject=subject,
        body=body,
        tags=tags,
    )
    observe_drift_d8_reply_path(
        thread_id=thread_id,
        from_agent=from_agent,
        to_agent=to_agent,
        tags=tags,
        prior_turn=prior_turn,
    )
    observe_drift_d7_bypass(
        thread_id=thread_id,
        from_agent=from_agent,
        subject=subject,
        body=body,
        tags=tags,
    )
    return advisory


__all__ = [
    "RouteControlEnvelope",
    "apply_ruling_gate",
    "assert_house_route_control",
    "drift_d7d8_mode",
    "envelope_enforce_mode",
    "gc1_acting_maestro_fact_set",
    "is_house_thread",
    "load_route_control_json_schema",
    "observe_drift_d7_bypass",
    "observe_drift_d8_reply_path",
    "parse_route_control_envelope",
    "requires_route_control_envelope",
    "route_control_drift_summary",
    "ruling_gate_mode",
    "validate_route_control_envelope",
    "would_envelope_reject_on_enforce",
    "would_ruling_gate_refuse_if_on",
]
