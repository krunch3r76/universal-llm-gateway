"""Ship gate attributes violations to selected_body only (offline)."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_PKG = "writing_v1_handlers"
if _PKG not in sys.modules:
    _HANDLERS = Path(__file__).resolve().parents[1] / "handlers"
    _spec = importlib.util.spec_from_file_location(
        _PKG,
        _HANDLERS / "__init__.py",
        submodule_search_locations=[str(_HANDLERS)],
    )
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _mod
    _spec.loader.exec_module(_mod)

finalize = importlib.import_module("writing_v1_handlers.finalize")
finalize_support = importlib.import_module("writing_v1_handlers.finalize_support")
provenance = importlib.import_module("writing_v1_handlers.provenance_check")
purpose_scrub = importlib.import_module("writing_v1_handlers.purpose_scrub")
_obligation = importlib.import_module("writing_v1_handlers._obligation_coverage")
source_obligations = importlib.import_module("writing_v1_handlers.source_obligations")

pytestmark = pytest.mark.offline

_MERIDIAL = (
    "Hi — we are hiring for an AI Trust and Safety role focused on policy enforcement. "
    "The team works on classifier quality and escalation review. "
    "If you are interested, apply here at https://meridial.ai/apply."
)

_CHASE_V1 = (
    "To: assessor@county.example\n"
    "Subject: Request for property tax review\n\n"
    "Hello,\n\n"
    "We are requesting a review of our property tax assessment for hardship reasons. "
    "Our household cannot afford the current tax burden after the recent reassessment. "
    "Please advise on the next steps with the Assessor's office.\n\n"
    "Best,\nPat"
)


class _Out:
    def __init__(
        self,
        payload: dict[str, Any] | None,
        *,
        raw: str = "",
        error: str | None = None,
    ) -> None:
        self.json = payload
        self.error = error
        self.raw = raw


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


def _run(outputs: dict[str, Any]) -> dict[str, Any]:
    payload = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    return payload


def _types(rows: list[Any]) -> set[str]:
    return {
        str(row.get("type"))
        for row in rows
        if isinstance(row, dict) and row.get("type")
    }


def test_rhythm_reverted_does_not_leak_v2_violations_into_unresolved() -> None:
    draft_v1 = _CHASE_V1
    draft_v2 = draft_v1 + "\n\n1. Dropped pin restatement.\n2. Invented fact."
    v2_only = [
        {"type": "restate_pin_missing", "claim_id": "P8", "detail": "v2 only"},
        {"type": "v2_dropped_name", "claim_id": None, "detail": "Pat"},
        {"type": "fact_dropped", "claim_id": None, "detail": "Pat"},
        {"type": "added_unattested_fact", "claim_id": None, "detail": "Invented"},
    ]
    payload = _run(
        {
            "assemble": _Out(
                {
                    "ok": True,
                    "refused": None,
                    "channel": "email",
                    "pin_ledger": [{"pin_id": "P1", "attested": True}],
                    "output": "envelope",
                }
            ),
            "draft": _Out({"draft": draft_v1, "ok": True}),
            "independence": _Out(
                {
                    "refused": None,
                    "writer_model": "openai/a",
                    "reviewer_model": "qwen/b",
                }
            ),
            "provenance_check": _Out(
                {
                    "violations": [],
                    "pass": True,
                    "drift_check": {"passed": True, "flagged_sentences": []},
                }
            ),
            "fact_ceiling_audit": _Out({"verdict": "ship", "findings": []}),
            "prose_rhythm_pass": _Out(None, raw=draft_v2),
            "fact_ceiling_final": _Out(
                {
                    "violations": v2_only,
                    "pass": False,
                    "drift_check": {
                        "passed": False,
                        "flagged_sentences": [
                            {"sentence": "Invented.", "reason": "not_purpose_relevant"}
                        ],
                    },
                }
            ),
            "source_obligations": _Out({"obligations": []}),
            "obligation_coverage": _Out({"coverage_map": []}),
            "prompt_architect": _Out({"architect_brief": ""}),
        }
    )
    gate = payload["ship_gate"]
    assert gate["selected"] == "draft_v1"
    assert gate["rhythm_reverted"] is True
    unresolved_types = _types(gate["unresolved"])
    assert "v2_dropped_name" not in unresolved_types
    assert "restate_pin_missing" not in unresolved_types
    assert gate.get("revision_discarded") == v2_only


def test_false_quality_violation_dropped_for_clean_correspondence_body() -> None:
    body = _CHASE_V1
    false_quality = {
        "type": "quality_violation",
        "claim_id": "no_numbered_or_bulleted_body",
        "detail": "Numbered list in email body",
    }
    payload = _run(
        {
            "assemble": _Out(
                {
                    "ok": True,
                    "refused": None,
                    "channel": "email",
                    "pin_ledger": [],
                    "output": "envelope",
                }
            ),
            "draft": _Out({"draft": body, "ok": True}),
            "independence": _Out(
                {
                    "refused": None,
                    "writer_model": "openai/a",
                    "reviewer_model": "qwen/b",
                }
            ),
            "provenance_check": _Out(
                {
                    "violations": [false_quality],
                    "pass": False,
                    "drift_check": {"passed": True, "flagged_sentences": []},
                }
            ),
            "fact_ceiling_audit": _Out({"verdict": "ship", "findings": []}),
            "source_obligations": _Out({"obligations": []}),
            "obligation_coverage": _Out({"coverage_map": []}),
            "prompt_architect": _Out({"architect_brief": ""}),
        }
    )
    assert "quality_violation" not in _types(payload["ship_gate"]["unresolved"])


def test_hardship_sentences_not_scrubbed_as_purpose_drift() -> None:
    objective = "Request property tax hardship review; explain affordability; ask Assessor next steps."
    notes = [
        "We cannot afford the current tax burden after the reassessment.",
    ]
    sentence = "Our household cannot afford the current tax burden after the recent reassessment."
    drift = purpose_scrub.scrub_purpose_restatement_flags(
        {
            "passed": False,
            "flagged_sentences": [
                {"sentence": sentence, "reason": "not_purpose_relevant"}
            ],
        },
        ["Parking instructions."],
        pin_ledger=[],
        purpose_text=objective,
        verbatim_notes=notes,
    )
    assert drift["passed"] is True
    assert drift["flagged_sentences"] == []


def test_meridial_next_steps_question_addresses_o1() -> None:
    outputs = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "intake": {"source_message": _MERIDIAL, "channel": "email"},
            }
        )
    }
    src = asyncio.run(
        source_obligations.WritingSourceObligationsHandler().execute(
            None, _Ctx(outputs)
        )
    ).json
    assert src is not None
    draft = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety role\n\n"
        "Thanks for reaching out. Could you let me know what the next steps are?\n\n"
        "Best,\nPat"
    )
    obligations = _obligation.actionable_obligations(src["obligations"])
    cov = _obligation.coverage_map_for_draft(draft, obligations, pin_ledger=[])
    apply_cov = next(
        (row for row in cov if row.get("obligation") == "act_outside_message"),
        None,
    )
    assert apply_cov is not None
    assert apply_cov["status"] == "addressed"


def test_quality_filter_unit() -> None:
    body = "Plain prose only.\n\nNo lists."
    row = {
        "type": "quality_violation",
        "claim_id": "bracket_placeholder",
        "detail": "Missing [3 / 6] placeholder",
    }
    assert finalize_support._quality_violation_applies_to_body(body, row) is False
