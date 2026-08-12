"""Additive status-register labels at cursor-auto terminal emission (arc 6655).

Bare status/disposition tokens stay unchanged for existing readers. Emission
adds sibling keys (JSON) or header lines (prose) naming *what register* each
token belongs to.

JSON chokepoint: ``post_terminal_status`` — dict sibling keys.
Prose chokepoint: ``post_operator_closeout`` — envelope header lines (not
JSON-key porting; prose readers scan lines, not index keys).

``disposition_hint`` is the *planned* register (contract policy at admit);
``disposition`` is the *observed* register (outcome at terminalization). They
may diverge (e.g. hint ``answered`` + outcome ``declined`` on empty answer).
"""

from __future__ import annotations

from typing import Any, Literal

# Planned register — contract policy from resolve_contract_disposition (admit).
DISPOSITION_HINT_STATUS_OF = "cursor_auto.bus_terminal.contract_policy"

# Observed register — reader-facing outcome token at terminalization.
# Value names the plane, not the planned-vs-observed moment (see DISPOSITION_HINT_STATUS_OF).
DISPOSITION_STATUS_OF = "cursor_auto.bus_terminal.disposition"

# Wait-subject completion token (``status:*`` in turn subject) names job terminalization.
TERMINAL_STATUS_STATUS_OF = "cursor_auto.bus_terminal.job_terminalization"

# Prose CLOSEOUT envelope ``status:`` line — relay measurement (≠ status_claim).
ENVELOPE_STATUS_STATUS_OF = "cursor_auto.closeout_relay.envelope_measurement"

# Contract-policy hint values from wire_map.resolve_contract_disposition (wire_map.py:423-437).
DISPOSITION_HINT_KNOWN: frozenset[str] = frozenset(
    {
        "answered",
        "conferred",
        "dispatched-and-relayed",
        "executed",
        "propagated",
    }
)

DispositionHintPresence = Literal["present_known", "present_out_of_set", "absent"]
DispositionHintLabelVerdict = Literal["label", "no_label_needed"]


def disposition_hint_presence(value: object) -> DispositionHintPresence:
    """Classify disposition_hint payload state without routing guarantees."""
    if value is None:
        return "absent"
    text = str(value).strip()
    if not text:
        return "absent"
    if text in DISPOSITION_HINT_KNOWN:
        return "present_known"
    return "present_out_of_set"


def disposition_hint_label_verdict(value: object) -> DispositionHintLabelVerdict:
    """Whether emission stamps ``disposition_hint_status_of`` for *value*."""
    return "label" if disposition_hint_presence(value) != "absent" else "no_label_needed"


# Observed outcome tokens that may appear on reader-facing ``disposition``.
DISPOSITION_OUTCOME_KNOWN: frozenset[str] = frozenset(
    {
        "answered",
        "conferred",
        "declined",
        "dispatched-and-relayed",
        "executed",
        "propagated",
        "blocked",
        "needs-attended",
        "expired",
        "failed",
        "superseded",
        "complete",
        "fence_violation",
        "harvest_wanted",
        "queued",
        "submitted",
    }
)

DispositionOutcomePresence = Literal["present_known", "present_out_of_set", "absent"]
DispositionOutcomeLabelVerdict = Literal["label", "no_label_needed"]


def disposition_outcome_presence(value: object) -> DispositionOutcomePresence:
    """Classify disposition payload state without routing guarantees."""
    if value is None:
        return "absent"
    text = str(value).strip()
    if not text:
        return "absent"
    if text in DISPOSITION_OUTCOME_KNOWN:
        return "present_known"
    return "present_out_of_set"


def disposition_outcome_label_verdict(value: object) -> DispositionOutcomeLabelVerdict:
    """Whether emission stamps ``disposition_status_of`` for *value*."""
    return "label" if disposition_outcome_presence(value) != "absent" else "no_label_needed"


def stamp_disposition_hint_status_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Return *payload* with additive ``disposition_hint_status_of`` when hint is set."""
    if "disposition_hint" not in payload:
        return payload
    if disposition_hint_label_verdict(payload.get("disposition_hint")) != "label":
        return payload
    if payload.get("disposition_hint_status_of") == DISPOSITION_HINT_STATUS_OF:
        return payload
    return {**payload, "disposition_hint_status_of": DISPOSITION_HINT_STATUS_OF}


def strip_disposition_hint_status_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop contract-policy register label when the hint omits."""
    if "disposition_hint_status_of" not in payload:
        return payload
    return {k: v for k, v in payload.items() if k != "disposition_hint_status_of"}


def stamp_disposition_status_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Return *payload* with additive ``disposition_status_of`` when disposition is set."""
    if "disposition" not in payload:
        return payload
    if disposition_outcome_label_verdict(payload.get("disposition")) != "label":
        return payload
    if payload.get("disposition_status_of") == DISPOSITION_STATUS_OF:
        return payload
    return {**payload, "disposition_status_of": DISPOSITION_STATUS_OF}


def strip_disposition_status_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop outcome register label when the outcome token omits."""
    if "disposition_status_of" not in payload:
        return payload
    return {k: v for k, v in payload.items() if k != "disposition_status_of"}


def stamp_terminal_status_status_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Return *payload* with additive ``terminal_status_status_of`` at emission."""
    if payload.get("terminal_status_status_of") == TERMINAL_STATUS_STATUS_OF:
        return payload
    return {**payload, "terminal_status_status_of": TERMINAL_STATUS_STATUS_OF}


def prose_closeout_register_header_lines() -> list[str]:
    """Additive prose header lines for ``post_operator_closeout`` envelopes.

    Insert after the envelope ``status:`` line. Does not rewrite that line or
    the bus subject. Named ``envelope_status_status_of`` (not ``status_status_of``)
    so ``startswith(\"status:\")`` body-line filters cannot conflate label and field.
    """
    return [
        f"envelope_status_status_of: {ENVELOPE_STATUS_STATUS_OF}",
        f"terminal_status_status_of: {TERMINAL_STATUS_STATUS_OF}",
    ]


def stamp_meta_terminal_status_status_of(meta: dict[str, Any]) -> dict[str, Any]:
    """Add ``terminal_status_status_of`` when meta carries ``terminal_status``.

    Value of ``terminal_status`` is unchanged — structural additivity only.
    """
    if "terminal_status" not in meta:
        return meta
    if meta.get("terminal_status_status_of") == TERMINAL_STATUS_STATUS_OF:
        return meta
    return {**meta, "terminal_status_status_of": TERMINAL_STATUS_STATUS_OF}
