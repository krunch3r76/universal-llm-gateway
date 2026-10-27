"""Provenance violations derived from semantic_judge rows."""

from __future__ import annotations

from typing import Any

from .email_envelope import email_restate_pin_satisfied
from .judge_winner_rows import winner_judge_rows
from .provenance_envelope import _is_non_claim_envelope_unit


def _violation(kind: str, claim_id: str | None, detail: Any) -> dict[str, Any]:
    return {"type": kind, "claim_id": claim_id, "detail": detail}


def judge_unavailable_violation() -> dict[str, Any]:
    return _violation("semantic_judge_unavailable", None, "judge_unavailable")


def violations_from_judge_pins(
    ledger: list[dict[str, Any]],
    judge_pins: list[dict[str, Any]],
    *,
    winner_id: str | None = None,
) -> list[dict[str, Any]]:
    """Map judge pin rows to restate_pin_missing for the selected candidate."""
    violations: list[dict[str, Any]] = []
    winner_pins = winner_judge_rows(judge_pins, winner_id)
    by_pin = {
        str(row.get("pin_id")): row
        for row in winner_pins
        if isinstance(row, dict) and row.get("pin_id")
    }
    for pin in ledger:
        if not isinstance(pin, dict):
            continue
        pin_id = str(pin.get("pin_id") or "")
        if not pin_id:
            continue
        restate = pin.get("restate") is True
        verbatim = pin.get("verbatim_required") is True
        if not (restate or verbatim):
            continue
        row = by_pin.get(pin_id)
        if row is None:
            violations.append(
                _violation("restate_pin_missing", pin_id, str(pin.get("excerpt") or "")[:120])
            )
            continue
        if row.get("satisfied") is False or row.get("polarity_match") is False:
            violations.append(
                _violation("restate_pin_missing", pin_id, str(pin.get("excerpt") or "")[:120])
            )
    return violations


def added_facts_from_judge_clauses(
    clauses: list[dict[str, Any]],
    *,
    channel: str | None = None,
    signer: str | None = None,
) -> list[str]:
    added: list[str] = []
    for row in clauses:
        if not isinstance(row, dict):
            continue
        if row.get("attested") is True:
            continue
        if row.get("speech_act") == "request":
            continue
        text = str(row.get("text") or "").strip()
        if not text or _is_non_claim_envelope_unit(text, channel=channel, signer=signer):
            continue
        added.append(text)
    return added


def clause_violations(
    clauses: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    *,
    channel: str | None = None,
    signer: str | None = None,
) -> list[dict[str, Any]]:
    """Flag unattested assertions and requests misclassified as assertions."""
    violations: list[dict[str, Any]] = []
    by_id = {
        str(pin.get("pin_id")): pin
        for pin in ledger
        if isinstance(pin, dict) and pin.get("pin_id")
    }
    for index, row in enumerate(clauses):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        speech = str(row.get("speech_act") or "")
        if speech == "request":
            continue
        if (
            row.get("attested") is False
            and text.strip()
            and not _is_non_claim_envelope_unit(text, channel=channel, signer=signer)
        ):
            violations.append(
                _violation("added_unattested_fact", f"c{index}", text[:120])
            )
        pin_ids = row.get("pin_ids") if isinstance(row.get("pin_ids"), list) else []
        for pin_id in pin_ids:
            pin = by_id.get(str(pin_id))
            if pin is None:
                violations.append(_violation("unknown_pin", f"c{index}", str(pin_id)))
            elif pin.get("attested") is False and speech != "request":
                violations.append(_violation("unattested_pin", f"c{index}", str(pin_id)))
    return violations


def verbatim_ledger_violations(
    ledger: list[dict[str, Any]],
    draft_text: str,
    *,
    pre_polish: str | None = None,
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    pre = pre_polish if pre_polish is not None else draft_text
    for pin in ledger:
        if not isinstance(pin, dict):
            continue
        if pin.get("verbatim_required") is not True:
            continue
        excerpt = str(pin.get("excerpt") or "")
        if not excerpt:
            continue
        ok = email_restate_pin_satisfied(pre, excerpt) or email_restate_pin_satisfied(
            draft_text, excerpt
        )
        if not ok:
            violations.append(
                _violation(
                    "verbatim_pin_modified",
                    str(pin.get("pin_id")),
                    excerpt[:120],
                )
            )
    return violations
