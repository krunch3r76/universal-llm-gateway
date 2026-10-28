"""Provenance-check tests for writer-specialist v1 (semantic_judge authority)."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
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

provenance = importlib.import_module("writing_v1_handlers.provenance_check")

pytestmark = pytest.mark.offline

_LEDGER = [
    {"pin_id": "P1", "attested": True, "excerpt": "backed", "restate": True},
    {"pin_id": "P2", "attested": False, "excerpt": "rumor"},
]


def _draft(text: str) -> dict[str, Any]:
    return {"draft": text, "claims": [], "need": [], "dispositions": []}


def _judge(
    *,
    pins: list[dict[str, Any]] | None = None,
    clauses: list[dict[str, Any]] | None = None,
    quality_findings: list[dict[str, Any]] | None = None,
    unavailable: bool = False,
) -> dict[str, Any]:
    return {
        "judge_unavailable": unavailable,
        "pins": pins or [],
        "clauses": clauses or [],
        "quality_findings": quality_findings or [],
    }


def _types(violations: list[dict]) -> list[str]:
    return [row["type"] for row in violations]


def test_judge_unavailable_violation() -> None:
    violations = provenance.check_provenance(
        _LEDGER, _draft("text"), _judge(unavailable=True)
    )
    assert _types(violations) == ["semantic_judge_unavailable"]


def test_winner_pin_row_wins_over_loser_for_restate() -> None:
    judge = _judge(
        pins=[
            {
                "pin_id": "P1",
                "candidate_id": "drafter_openai",
                "satisfied": False,
                "polarity_match": True,
                "evidence_span": "",
            },
            {
                "pin_id": "P1",
                "candidate_id": "drafter_anthropic",
                "satisfied": True,
                "polarity_match": True,
                "evidence_span": "backed",
            },
        ]
    )
    violations = provenance.check_provenance(
        _LEDGER, _draft("text"), judge, winner_id="drafter_openai"
    )
    assert "restate_pin_missing" in _types(violations)


def test_fact_reordered_when_v1_and_v2_pin_orders_differ() -> None:
    v1 = _draft("Same body.")
    v2 = _draft("Same body.")
    v1_clauses = [{"pin_ids": ["P2", "P1"], "text": "a", "attested": True}]
    v2_clauses = [{"pin_ids": ["P1", "P2"], "text": "a", "attested": True}]
    violations = provenance.revision_delta(
        v1,
        v2,
        v1_clauses=v1_clauses,
        v2_clauses=v2_clauses,
        channel="filing",
    )
    assert "fact_reordered" in _types(violations)


def test_fact_reordered_silent_when_pin_orders_match() -> None:
    v1 = _draft("Same body.")
    v2 = _draft("Same body.")
    clauses = [{"pin_ids": ["P1", "P2"], "text": "a", "attested": True}]
    violations = provenance.revision_delta(
        v1,
        v2,
        v1_clauses=clauses,
        v2_clauses=list(clauses),
        channel="filing",
    )
    assert "fact_reordered" not in _types(violations)


def test_v2_judge_pin_rows_without_candidate_id_kept_at_winner_filter() -> None:
    """fact_ceiling_final: v2 semantic_judge rows omit candidate_id."""
    judge = _judge(
        pins=[
            {
                "pin_id": "P1",
                "satisfied": True,
                "polarity_match": True,
                "evidence_span": "backed",
            }
        ]
    )
    violations = provenance.check_provenance(
        _LEDGER, _draft("text"), judge, winner_id="drafter_anthropic"
    )
    assert "restate_pin_missing" not in _types(violations)


def test_fact_ceiling_final_handler_honors_v2_judge_pins() -> None:
    class _Out:
        def __init__(self, payload: dict | None, *, raw: str = "") -> None:
            self.json = payload
            self.raw = raw

    class _Ctx:
        def __init__(self, outputs: dict) -> None:
            self.outputs = outputs

    class _Step:
        handler_inputs = {
            "pin_ledger": "assemble.json.pin_ledger",
            "draft": "prose_rhythm_pass.raw",
            "draft_v1": "draft.json",
            "judge": "semantic_judge_v2.json",
        }

    v2_judge = _judge(
        pins=[
            {
                "pin_id": "P1",
                "satisfied": True,
                "polarity_match": True,
                "evidence_span": "backed",
            }
        ]
    )
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _Step(),
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "channel": "filing",
                            "pin_ledger": _LEDGER,
                            "intake": {},
                        }
                    ),
                    "draft": _Out(
                        {
                            "selected_step": "drafter_anthropic",
                            "draft": "text",
                            "claims": [],
                        }
                    ),
                    "prose_rhythm_pass": _Out(None, raw='{"draft": "text"}'),
                    "semantic_judge_v2": _Out(v2_judge),
                }
            ),
        )
    ).json
    assert "restate_pin_missing" not in _types(payload["violations"])


def test_v2_quality_finding_surfaces_quality_violation() -> None:
    legalistic = "I, Jane Doe, am submitting this correspondence regarding the matter."
    judge = _judge(
        pins=[
            {
                "pin_id": "P1",
                "satisfied": True,
                "polarity_match": True,
                "evidence_span": "ok",
            }
        ],
        quality_findings=[
            {
                "rule": "legalistic_opening",
                "detail": "sworn-style opening",
                "penalty": 2,
            }
        ],
    )
    violations = provenance.check_provenance(
        _LEDGER, _draft(legalistic), judge, winner_id="drafter_anthropic"
    )
    assert "quality_violation" in _types(violations)


def test_fact_ceiling_final_handler_surfaces_v2_quality_violation() -> None:
    class _Out:
        def __init__(self, payload: dict | None, *, raw: str = "") -> None:
            self.json = payload
            self.raw = raw

    class _Ctx:
        def __init__(self, outputs: dict) -> None:
            self.outputs = outputs

    class _Step:
        handler_inputs = {
            "pin_ledger": "assemble.json.pin_ledger",
            "draft": "prose_rhythm_pass.raw",
            "draft_v1": "draft.json",
            "judge": "semantic_judge_v2.json",
        }

    body = "- item one\n- item two"
    v2_judge = _judge(
        pins=[
            {
                "pin_id": "P1",
                "satisfied": True,
                "polarity_match": True,
                "evidence_span": "ok",
            }
        ],
        quality_findings=[
            {
                "rule": "bulleted_body",
                "detail": "unordered list in email body",
                "penalty": 1,
            }
        ],
    )
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _Step(),
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "channel": "email",
                            "pin_ledger": _LEDGER,
                            "intake": {"signer": "Jane"},
                        }
                    ),
                    "draft": _Out(
                        {
                            "selected_step": "drafter_anthropic",
                            "draft": body,
                            "claims": [],
                        }
                    ),
                    "prose_rhythm_pass": _Out(None, raw=json.dumps({"draft": body})),
                    "semantic_judge_v2": _Out(v2_judge),
                }
            ),
        )
    ).json
    assert payload["pass"] is False
    assert "quality_violation" in _types(payload["violations"])


def test_restate_pin_missing_from_judge_row() -> None:
    judge = _judge(
        pins=[
            {
                "pin_id": "P1",
                "candidate_id": "drafter_anthropic",
                "satisfied": False,
                "polarity_match": True,
                "evidence_span": "",
            }
        ]
    )
    violations = provenance.check_provenance(
        _LEDGER, _draft("text"), judge, winner_id="drafter_anthropic"
    )
    assert "restate_pin_missing" in _types(violations)


def test_ledger_restate_pin_absent_from_judge_rows() -> None:
    violations = provenance.check_provenance(_LEDGER, _draft("text"), _judge())
    assert "restate_pin_missing" in _types(violations)


def test_unattested_clause_not_treated_as_request() -> None:
    ledger = [{"pin_id": "P2", "attested": False, "excerpt": "rumor"}]
    judge = _judge(
        clauses=[
            {
                "text": "Isn't it true you terminated me?",
                "pin_ids": [],
                "speech_act": "assertion",
                "attested": False,
            }
        ]
    )
    violations = provenance.check_provenance(ledger, _draft("Q?"), judge)
    assert "added_unattested_fact" in _types(violations)


def test_revision_delta_dropped_name_and_number() -> None:
    v1 = _draft("Contact Ada Lovelace on 42.")
    v2 = _draft("Contact someone else.")
    violations = provenance.revision_delta(v1, v2, v2_clauses=[])
    kinds = _types(violations)
    assert "v2_dropped_name" in kinds
    assert "v2_dropped_number" in kinds
    assert "fact_dropped" in kinds


def test_revision_delta_unattested_v2_clause() -> None:
    v1 = _draft("Same.")
    v2 = _draft("Same. New unattested claim added here.")
    clauses = [
        {
            "text": "New unattested claim added here.",
            "pin_ids": [],
            "speech_act": "assertion",
            "attested": False,
        }
    ]
    violations = provenance.revision_delta(v1, v2, v2_clauses=clauses)
    assert "added_unattested_fact" in _types(violations)


def test_handler_email_subject_missing() -> None:
    class _Out:
        def __init__(self, payload: dict) -> None:
            self.json = payload

    class _Ctx:
        def __init__(self, outputs: dict) -> None:
            self.outputs = outputs

    class _Step:
        handler_inputs = {
            "pin_ledger": "assemble.json.pin_ledger",
            "draft": "draft.json",
            "judge": "semantic_judge_candidates.json",
        }

    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _Step(),
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "channel": "email",
                            "pin_ledger": [],
                            "intake": {"objective": "confirm"},
                        }
                    ),
                    "draft": _Out({"draft": "Hello without subject.", "claims": []}),
                    "semantic_judge_candidates": _Out(_judge()),
                }
            ),
        )
    ).json
    assert "email_subject_missing" in _types(payload["violations"])


class _HandlerOut:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.json = payload


class _HandlerCtx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


class _HandlerStep:
    handler_inputs = {
        "pin_ledger": "assemble.json.pin_ledger",
        "draft": "draft.json",
        "judge": "semantic_judge_candidates.json",
    }


_EMPTY_JUDGE = {"pins": [], "clauses": [], "judge_unavailable": False}


def test_handler_scrubs_objective_restatement_from_drift_check() -> None:
    sentence = "The Assessor received the supplemental increase request."
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _HandlerStep(),
            _HandlerCtx(
                {
                    "assemble": _HandlerOut(
                        {
                            "channel": "email",
                            "pin_ledger": [],
                            "purpose_points": ["Parking instructions."],
                            "intake": {
                                "objective": (
                                    "Ask the Assessor to review the supplemental increase."
                                ),
                            },
                        }
                    ),
                    "draft": _HandlerOut({"draft": sentence, "claims": []}),
                    "semantic_judge_candidates": _HandlerOut(_EMPTY_JUDGE),
                }
            ),
        )
    ).json
    drift = payload["drift_check"]
    assert drift["passed"] is True
    assert drift["flagged_sentences"] == []


def test_handler_scrubs_verbatim_note_restatement_from_drift_check() -> None:
    note = "The property is at 669 Second Street."
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _HandlerStep(),
            _HandlerCtx(
                {
                    "assemble": _HandlerOut(
                        {
                            "channel": "email",
                            "pin_ledger": [],
                            "purpose_points": ["Unrelated point."],
                            "intake": {
                                "objective": "Confirm the hearing date.",
                                "verbatim_notes": [note],
                            },
                        }
                    ),
                    "draft": _HandlerOut({"draft": note, "claims": []}),
                    "semantic_judge_candidates": _HandlerOut(_EMPTY_JUDGE),
                }
            ),
        )
    ).json
    drift = payload["drift_check"]
    assert drift["passed"] is True
    assert drift["flagged_sentences"] == []


def test_phone_purpose_line_not_flagged_as_drift() -> None:
    purpose_scrub = importlib.import_module("writing_v1_handlers.purpose_scrub")
    sentence = "You can reach me at (669) 871-3234."
    points = ["Phone number for reply."]
    ledger = [{"excerpt": "669-871-3234"}]
    assert purpose_scrub.sentence_restates_purpose_point(
        sentence, points, pin_ledger=ledger
    )
    drift = purpose_scrub.scrub_purpose_restatement_flags(
        {
            "passed": False,
            "flagged_sentences": [
                {"sentence": sentence, "reason": "not_purpose_relevant"}
            ],
        },
        points,
        pin_ledger=ledger,
        purpose_text="",
        verbatim_notes=[],
    )
    assert drift["passed"] is True
    assert drift["flagged_sentences"] == []


def test_phone_without_ledger_stays_flagged() -> None:
    purpose_scrub = importlib.import_module("writing_v1_handlers.purpose_scrub")
    sentence = "You can reach me at (669) 871-3234."
    points = ["Phone number for reply."]
    assert not purpose_scrub.sentence_restates_purpose_point(
        sentence, points, pin_ledger=[]
    )
    drift = purpose_scrub.scrub_purpose_restatement_flags(
        {
            "passed": False,
            "flagged_sentences": [
                {"sentence": sentence, "reason": "not_purpose_relevant"}
            ],
        },
        points,
        pin_ledger=[],
        purpose_text="",
        verbatim_notes=[],
    )
    assert drift["passed"] is False
    assert drift["flagged_sentences"]


def test_scrub_drops_objective_restatement() -> None:
    purpose_scrub = importlib.import_module("writing_v1_handlers.purpose_scrub")
    sentence = "The Assessor received the supplemental increase request."
    drift = purpose_scrub.scrub_purpose_restatement_flags(
        {
            "passed": False,
            "flagged_sentences": [
                {"sentence": sentence, "reason": "not_purpose_relevant"}
            ],
        },
        ["Parking instructions."],
        pin_ledger=[],
        purpose_text="Ask the Assessor to review the supplemental increase.",
        verbatim_notes=[],
    )
    assert drift["passed"] is True
    assert drift["flagged_sentences"] == []


def test_scrub_drops_verbatim_note_restatement() -> None:
    purpose_scrub = importlib.import_module("writing_v1_handlers.purpose_scrub")
    note = "The property is at 669 Second Street."
    drift = purpose_scrub.scrub_purpose_restatement_flags(
        {
            "passed": False,
            "flagged_sentences": [{"sentence": note, "reason": "not_purpose_relevant"}],
        },
        ["Unrelated point."],
        pin_ledger=[],
        purpose_text="Confirm the hearing date.",
        verbatim_notes=[note],
    )
    assert drift["passed"] is True
    assert drift["flagged_sentences"] == []


def test_scrub_skipped_when_no_purpose_context() -> None:
    purpose_scrub = importlib.import_module("writing_v1_handlers.purpose_scrub")
    note = "The property is at 669 Second Street."
    drift_in = {
        "passed": False,
        "flagged_sentences": [{"sentence": note, "reason": "not_purpose_relevant"}],
    }
    drift = purpose_scrub.scrub_purpose_restatement_flags(
        drift_in,
        [],
        pin_ledger=[],
        purpose_text="",
        verbatim_notes=[note],
    )
    assert drift is drift_in


def test_scrub_passed_equals_not_flagged() -> None:
    purpose_scrub = importlib.import_module("writing_v1_handlers.purpose_scrub")
    drift = purpose_scrub.scrub_purpose_restatement_flags(
        {
            "passed": False,
            "flagged_sentences": [
                {
                    "sentence": "Leftover.",
                    "reason": "declaration_register_in_correspondence",
                }
            ],
        },
        ["Parking instructions."],
        pin_ledger=[],
        purpose_text="",
        verbatim_notes=[],
    )
    assert drift["passed"] == (not drift["flagged_sentences"])


def test_scrub_recomputes_passed_when_all_not_purpose_rows_dropped() -> None:
    purpose_scrub = importlib.import_module("writing_v1_handlers.purpose_scrub")
    drift = purpose_scrub.scrub_purpose_restatement_flags(
        {
            "passed": False,
            "flagged_sentences": [],
        },
        ["Parking instructions."],
        pin_ledger=[],
        purpose_text="Ask the Assessor to review the supplemental increase.",
        verbatim_notes=[],
    )
    assert drift["passed"] is True
    assert drift["flagged_sentences"] == []


def test_em_dash_retained() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft("Left \u2014 right."),
        _judge(),
    )
    assert "em_dash" in _types(violations)
