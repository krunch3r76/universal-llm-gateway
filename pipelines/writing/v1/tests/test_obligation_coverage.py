"""Obligation ledger, coverage audit, finalize gate, and drafter fanout (offline)."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from systems.pipeline.core.conditions import ConditionEvaluator
from systems.pipeline.core.handlers.step_output import StepOutput
from systems.pipeline.core.pipeline_config import PipelineSpec

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

source_obligations = importlib.import_module("writing_v1_handlers.source_obligations")
obligation_coverage = importlib.import_module("writing_v1_handlers.obligation_coverage")
finalize = importlib.import_module("writing_v1_handlers.finalize")
_obligation = importlib.import_module("writing_v1_handlers._obligation_coverage")

pytestmark = pytest.mark.offline

_V1 = Path(__file__).resolve().parents[1]
_MERIDIAL = (
    "Hi — we are hiring for an AI Trust and Safety role focused on policy enforcement. "
    "The team works on classifier quality and escalation review. "
    "If you are interested, apply here at https://meridial.ai/apply."
)
_FANOUT = (
    "assemble.json.get('refused') is None and options.get('output', 'envelope') != 'packet' "
    "and (options.get('full_drafter_fanout', False) is True or assemble.json.get('channel') == 'filing')"
)
_GO = (
    "assemble.json.get('refused') is None and "
    "options.get('output', 'envelope') != 'packet'"
)


class _Out:
    def __init__(self, payload: dict[str, Any] | None, *, raw: str = "") -> None:
        self.json = payload
        self.error = None
        self.raw = raw
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.model_id = None


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


def _run_source(message: str) -> dict[str, Any]:
    outputs = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "intake": {"source_message": message, "channel": "email"},
            }
        )
    }
    payload = asyncio.run(
        source_obligations.WritingSourceObligationsHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    return payload


def test_meridial_apply_here_classified_as_act_outside_message() -> None:
    payload = _run_source(_MERIDIAL)
    apply_rows = [
        row
        for row in payload["obligations"]
        if "apply here" in str(row.get("quoted_span", "")).lower()
        or row.get("obligation") == "act_outside_message"
    ]
    assert apply_rows, payload["obligations"]
    row = apply_rows[0]
    assert row["kind"] == "call_to_action"
    assert row["obligation"] == "act_outside_message"


def test_meridial_before_after_drafter_calls_and_tokens_saved() -> None:
    raw = yaml.safe_load((_V1 / "writer-specialist-v1.yaml").read_text(encoding="utf-8"))
    by_name = {step.name: step for step in PipelineSpec(**raw).steps}
    evaluator = ConditionEvaluator()

    def step(data: dict[str, Any]) -> StepOutput:
        return StepOutput(raw="", json=data)

    ctx = {"assemble": step({"refused": None, "channel": "email"})}
    options = {"output": "envelope", "full_drafter_fanout": False}
    all_drafters = (
        "drafter_openai",
        "drafter_google",
        "drafter_xai",
        "drafter_local_hermes",
        "drafter_local_ernie",
        "drafter_anthropic",
    )
    active = [
        name
        for name in all_drafters
        if evaluator.evaluate(by_name[name].condition, ctx, options)
    ]
    skipped = set(all_drafters) - set(active)
    assert skipped == {
        "drafter_openai",
        "drafter_google",
        "drafter_xai",
        "drafter_local_ernie",
    }
    assert len(active) == 2
    tokens_per_drafter = 500
    usage_before = {name: tokens_per_drafter for name in all_drafters}
    usage_after = {name: tokens_per_drafter for name in active}
    assert sum(usage_before.values()) - sum(usage_after.values()) == 4 * tokens_per_drafter

    src = _run_source(_MERIDIAL)
    draft_text = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "Thank you for the AI Trust and Safety outreach. "
        "I submitted my application through https://meridial.ai/apply today.\n\n"
        "Best,\nPat"
    )
    outputs = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "channel": "email",
                "pin_ledger": [{"pin_id": "P1", "attested": True}],
                "writer_seat": "local",
                "reviewer_seat": "local",
                "output": "envelope",
            }
        ),
        "source_obligations": _Out({"obligations": src["obligations"]}),
        "draft": _Out({"ok": True, "draft": draft_text}),
        "independence": _Out(
            {
                "refused": None,
                "writer_model": "anthropic/claude-sonnet-5-5",
                "reviewer_model": "qwen3-5-9b-q8-0-131072",
            }
        ),
        "fact_ceiling_audit": _Out({"verdict": "ship", "findings": []}),
        "provenance_check": _Out({"violations": []}),
        "prose_rhythm_pass": _Out(None, raw=draft_text),
        "fact_ceiling_final": _Out({"violations": []}),
    }
    payload = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    apply_cov = next(
        (
            row
            for row in payload.get("coverage_map") or []
            if row.get("obligation") == "act_outside_message"
            or "apply" in str(row.get("covering_sentence", "")).lower()
        ),
        None,
    )
    assert apply_cov is not None
    assert apply_cov["kind"] == "call_to_action"
    assert apply_cov["obligation"] == "act_outside_message"
    assert apply_cov["reply_mode"] == "act_then_confirm"
    assert apply_cov["status"] == "addressed"
    assert apply_cov.get("covering_sentence")


def test_filing_omit_with_reason_never_obligation_refuses() -> None:
    obligations = [
        {
            "id": "O1",
            "quoted_span": "Please confirm receipt.",
            "kind": "request",
            "obligation": "answer",
            "semantic_reading": "confirm receipt",
        }
    ]
    draft = "1. Exhibit-backed paragraph only.\n"
    outputs = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "channel": "filing",
                "pin_ledger": [{"pin_id": "P1", "attested": True}],
                "writer_seat": "local",
                "reviewer_seat": "local",
                "output": "envelope",
            }
        ),
        "prompt_architect": _Out(
            {
                "architect_brief": "obligation_map: O1: omit_with_reason",
            }
        ),
        "source_obligations": _Out({"obligations": obligations}),
        "draft": _Out({"ok": True, "draft": draft}),
        "independence": _Out(
            {
                "refused": None,
                "writer_model": "anthropic/claude-sonnet-5-5",
                "reviewer_model": "qwen3-5-9b-q8-0-131072",
            }
        ),
        "fact_ceiling_audit": _Out({"verdict": "ship", "findings": []}),
        "provenance_check": _Out({"violations": []}),
        "prose_rhythm_pass": _Out(None, raw=draft),
        "fact_ceiling_final": _Out({"violations": []}),
    }
    payload = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    assert payload.get("refused") != "obligation_coverage_failed"
    row = (payload.get("coverage_map") or [{}])[0]
    assert row.get("reply_mode") == "omit_with_reason"
    assert row.get("status") == "declined_with_reason"


def test_meridial_finalize_coverage_map_addresses_application_action() -> None:
    src = _run_source(_MERIDIAL)
    draft_text = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "Thank you for reaching out about the AI Trust and Safety role. "
        "I submitted my application through https://meridial.ai/apply today.\n\n"
        "Best,\nPat"
    )
    cov = _obligation.coverage_map_for_draft(
        draft_text, _obligation.actionable_obligations(src["obligations"])
    )
    assert cov
    apply_cov = next(
        (row for row in cov if row.get("obligation") == "act_outside_message"),
        None,
    )
    assert apply_cov is not None
    assert apply_cov["kind"] == "call_to_action"
    assert apply_cov["status"] in {"addressed", "declined_with_reason"}
    assert apply_cov["reply_mode"] in {"act_then_confirm", "ask_back", "answer_in_message"}
    assert apply_cov.get("covering_sentence")


def test_two_drafter_default_vs_six_on_correspondence() -> None:
    raw = yaml.safe_load((_V1 / "writer-specialist-v1.yaml").read_text(encoding="utf-8"))
    by_name = {step.name: step for step in PipelineSpec(**raw).steps}
    evaluator = ConditionEvaluator()

    def step(data: dict[str, Any]) -> StepOutput:
        return StepOutput(raw="", json=data)

    ctx = {"assemble": step({"refused": None, "channel": "email"})}
    options = {"output": "envelope", "full_drafter_fanout": False}
    active = [
        name
        for name in (
            "drafter_openai",
            "drafter_google",
            "drafter_xai",
            "drafter_local_hermes",
            "drafter_local_ernie",
            "drafter_anthropic",
        )
        if evaluator.evaluate(by_name[name].condition, ctx, options)
    ]
    assert active == ["drafter_local_hermes", "drafter_anthropic"]
    options["full_drafter_fanout"] = True
    active_all = [
        name
        for name in (
            "drafter_openai",
            "drafter_google",
            "drafter_xai",
            "drafter_local_hermes",
            "drafter_local_ernie",
            "drafter_anthropic",
        )
        if evaluator.evaluate(by_name[name].condition, ctx, options)
    ]
    assert len(active_all) == 6


def test_three_ask_missing_one_refuses_finalize() -> None:
    inbound = (
        "What is your hourly rate? "
        "Can you start on Monday? "
        "Please send your portfolio link."
    )
    obligations = source_obligations.fallback_obligations(inbound)
    assert len([o for o in obligations if o.get("obligation") != "none"]) >= 3
    draft_v1 = (
        "To: client@example.com\nSubject: Re: your questions\n\n"
        "My rate is $200 per hour. I can start on Monday.\n\nThanks,\nPat"
    )
    outputs = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "channel": "email",
                "pin_ledger": [{"pin_id": "P1", "attested": True}],
                "writer_seat": "local",
                "reviewer_seat": "local",
                "output": "envelope",
            }
        ),
        "source_obligations": _Out({"obligations": obligations}),
        "draft": _Out({"ok": True, "draft": draft_v1}),
        "independence": _Out({"refused": None, "writer_model": "local/a", "reviewer_model": "local/b"}),
        "fact_ceiling_audit": _Out({"verdict": "ship", "findings": []}),
        "provenance_check": _Out({"violations": []}),
        "prose_rhythm_pass": _Out(None, raw=draft_v1),
        "fact_ceiling_final": _Out({"violations": []}),
    }
    payload = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    assert payload.get("status") == "refused"
    assert payload.get("refused") == "obligation_coverage_failed"


def test_obligation_coverage_handler_emits_missing_block() -> None:
    inbound = "Can you confirm the meeting time?"
    obligations = source_obligations.fallback_obligations(inbound)
    outputs = {
        "assemble": _Out({"ok": True, "refused": None}),
        "source_obligations": _Out(
            {"obligations": obligations, "source_obligations_block": "<source_obligations/>"}
        ),
        "draft": _Out({"ok": True, "draft": "To: x\nSubject: y\n\nNo answer here.\n"}),
    }
    payload = asyncio.run(
        obligation_coverage.WritingObligationCoverageHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    assert payload.get("missing_count", 0) >= 1
    assert "missing_obligations" in (payload.get("missing_obligations_block") or "")
