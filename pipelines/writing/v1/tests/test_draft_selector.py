"""Draft selector scoring tests (offline, semantic_judge rows)."""

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

draft_selector = importlib.import_module("writing_v1_handlers.draft_selector")

pytestmark = pytest.mark.offline


class _Out:
    def __init__(
        self,
        raw: str = "",
        *,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
        model_id: str = "local-test",
    ) -> None:
        self.raw = raw
        self.json = payload
        self.error = error
        self.model_id = model_id


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


def _judge_for(
    *candidate_ids,
    pin_satisfied: bool = True,
    quality_penalty: int = 0,
) -> dict[str, Any]:
    pins = [
        {
            "candidate_id": cid,
            "pin_id": "P1",
            "satisfied": pin_satisfied,
            "polarity_match": True,
            "evidence_span": "x",
        }
        for cid in candidate_ids
    ]
    findings = [
        {
            "candidate_id": cid,
            "rule": "register",
            "detail": "test",
            "penalty": quality_penalty,
        }
        for cid in candidate_ids
        if quality_penalty
    ]
    return {"pins": pins, "clauses": [], "quality_findings": findings}


def _select(outputs: dict[str, Any]) -> dict[str, Any]:
    if "semantic_judge_candidates" not in outputs:
        ids = [
            key
            for key in outputs
            if key.startswith("drafter_") and not outputs[key].error
        ]
        outputs = {
            **outputs,
            "semantic_judge_candidates": _Out(
                payload=_judge_for(*ids),
            ),
        }
    payload = asyncio.run(
        draft_selector.WritingDraftSelectorHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    return payload


def test_judge_pin_score_penalizes_failed_pin() -> None:
    attested = [{"pin_id": "P1", "attested": True, "excerpt": "fact"}]
    pins = [
        {
            "candidate_id": "drafter_openai",
            "pin_id": "P1",
            "satisfied": False,
            "polarity_match": False,
            "evidence_span": "",
        }
    ]
    score, penalty = draft_selector._judge_pin_score(
        "drafter_openai", attested, pins, channel="email", draft_text="fact"
    )
    assert score == 0
    assert penalty == 1


def test_quality_penalty_sums_findings() -> None:
    findings = [
        {"candidate_id": "drafter_openai", "rule": "register", "detail": "x", "penalty": 3}
    ]
    assert draft_selector._quality_penalty("drafter_openai", findings) == 3


def test_selector_rejects_unresolved_need_token() -> None:
    payload = _select(
        {
            "assemble": _Out(payload={"pin_ledger": []}),
            "drafter_openai": _Out("I need [NEED: missing fact]."),
        }
    )
    assert payload.get("refused") == "unresolved_need_placeholder"


def test_selector_drops_candidate_omitted_from_judge() -> None:
    payload = _select(
        {
            "assemble": _Out(payload={"pin_ledger": []}),
            "drafter_openai": _Out("OpenAI draft."),
            "drafter_google": _Out("Google draft."),
            "semantic_judge_candidates": _Out(
                payload=_judge_for("drafter_openai"),
            ),
        }
    )
    assert payload["selected_step"] == "drafter_openai"


def test_selector_quality_penalty_lowers_rank() -> None:
    pins = [{"pin_id": "P1", "attested": True, "excerpt": "shared fact"}]
    payload = _select(
        {
            "assemble": _Out(payload={"pin_ledger": pins}),
            "drafter_openai": _Out("I state shared fact."),
            "drafter_google": _Out("I state shared fact."),
            "semantic_judge_candidates": _Out(
                payload={
                    "pins": [
                        {
                            "candidate_id": "drafter_openai",
                            "pin_id": "P1",
                            "satisfied": True,
                            "polarity_match": True,
                            "evidence_span": "x",
                        },
                        {
                            "candidate_id": "drafter_google",
                            "pin_id": "P1",
                            "satisfied": True,
                            "polarity_match": True,
                            "evidence_span": "x",
                        },
                    ],
                    "clauses": [],
                    "quality_findings": [
                        {
                            "candidate_id": "drafter_google",
                            "rule": "register",
                            "detail": "cold",
                            "penalty": 3,
                        }
                    ],
                }
            ),
        }
    )
    assert payload["selected_step"] == "drafter_openai"
