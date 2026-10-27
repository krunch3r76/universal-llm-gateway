"""Provenance-check tests for writer-specialist v1 (semantic_judge authority)."""

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
    unavailable: bool = False,
) -> dict[str, Any]:
    return {
        "judge_unavailable": unavailable,
        "pins": pins or [],
        "clauses": clauses or [],
    }


def _types(violations: list[dict]) -> list[str]:
    return [row["type"] for row in violations]


def test_judge_unavailable_violation() -> None:
    violations = provenance.check_provenance(
        _LEDGER, _draft("text"), _judge(unavailable=True)
    )
    assert _types(violations) == ["semantic_judge_unavailable"]


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


def test_em_dash_retained() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft("Left \u2014 right."),
        _judge(),
    )
    assert "em_dash" in _types(violations)
