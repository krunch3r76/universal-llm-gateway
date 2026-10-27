"""Obligation ledger, coverage audit, finalize gate, and drafter fanout (offline)."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
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
    def __init__(
        self,
        payload: dict[str, Any] | None,
        *,
        raw: str = "",
        model_id: str | None = None,
    ) -> None:
        self.json = payload
        self.error = None
        self.raw = raw
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.model_id = model_id


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
        "drafter_local_hermes",
        "drafter_local_ernie",
    }
    assert active == ["drafter_anthropic"]
    tokens_per_drafter = 500
    usage_before = {name: tokens_per_drafter for name in all_drafters}
    usage_after = {name: tokens_per_drafter for name in active}
    assert sum(usage_before.values()) - sum(usage_after.values()) == 5 * tokens_per_drafter

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
                "pin_ledger": [
                    {
                        "pin_id": "P1",
                        "attested": True,
                        "excerpt": (
                            "I submitted my application through "
                            "https://meridial.ai/apply today."
                        ),
                    }
                ],
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


def _omit_with_reason_finalize(
    *,
    channel: str,
    architect_brief: str,
    draft: str,
) -> dict[str, Any]:
    obligations = [
        {
            "id": "O1",
            "quoted_span": "Please send your monthly availability list.",
            "kind": "request",
            "obligation": "answer",
            "semantic_reading": "standing monthly availability list",
        }
    ]
    outputs = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "channel": channel,
                "pin_ledger": [{"pin_id": "P1", "attested": True}],
                "writer_seat": "local",
                "reviewer_seat": "local",
                "output": "envelope",
            }
        ),
        "prompt_architect": _Out({"architect_brief": architect_brief}),
        "source_obligations": _Out({"obligations": obligations}),
        "draft": _Out(
            {"ok": True, "draft": draft},
            model_id="hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid",
        ),
        "independence": _Out({"refused": None, "independence": "full"}),
        "fact_ceiling_audit": _Out(
            {"verdict": "ship", "findings": []},
            model_id="qwen3-14b-q4-k-m-40960",
        ),
        "provenance_check": _Out({"violations": []}),
        "prose_rhythm_pass": _Out(None, raw=draft),
        "fact_ceiling_final": _Out({"violations": []}),
    }
    payload = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    return payload


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_correspondence_omit_with_reason_proximity_passes_ship_gate(
    channel: str,
) -> None:
    body = (
        "Maria, I can't take those Petaluma shifts. Thanks for sending them. Kaywan"
    )
    if channel == "email":
        draft = f"To: Maria\nSubject: Petaluma shifts\n\n{body}"
    else:
        draft = body
    brief = "O1 (request): omit_with_reason — operator replying per-offer only"
    payload = _omit_with_reason_finalize(
        channel=channel, architect_brief=brief, draft=draft
    )
    assert payload.get("refused") != "obligation_coverage_failed"
    ship = payload.get("ship_gate") or {}
    assert ship.get("pass") is True
    row = next(
        (r for r in payload.get("coverage_map") or [] if r.get("id") == "O1"),
        None,
    )
    assert row is not None
    assert row.get("reply_mode") == "omit_with_reason"
    assert row.get("status") == "declined_with_reason"


def test_architect_omit_proximity_does_not_cross_next_obligation_line() -> None:
    brief = (
        "- O1: answer_in_message (decline offer)\n"
        "- O2: omit_with_reason (per-offer only)"
    )
    omit_ids = finalize._omit_with_reason_ids(brief)
    assert "O1" not in omit_ids
    assert "O2" in omit_ids


def test_architect_omit_o2_unanswered_o1_fails_coverage_gate() -> None:
    obligations = [
        {
            "id": "O1",
            "quoted_span": "Can you cover Petaluma?",
            "kind": "request",
            "obligation": "decline_with_reason",
            "semantic_reading": "decline offer",
        },
        {
            "id": "O2",
            "quoted_span": "Please send your monthly availability list.",
            "kind": "request",
            "obligation": "answer",
            "semantic_reading": "standing monthly availability list",
        },
    ]
    brief = (
        "- O1: answer_in_message (decline offer)\n"
        "- O2: omit_with_reason (per-offer only)"
    )
    cov_map, refusal = finalize._obligation_coverage_gate(
        "Thanks for reaching out.",
        obligations,
        selected="draft_v1",
        audit_map=None,
        revision_ran=False,
        architect_brief=brief,
        channel="sms",
    )
    by_id = {str(r.get("id")): r for r in cov_map}
    assert by_id["O1"].get("status") == "missing"
    assert by_id["O1"].get("reply_mode") != "omit_with_reason"
    assert by_id["O2"].get("reply_mode") == "omit_with_reason"
    assert refusal is not None


def test_architect_omit_o2_answered_o1_passes_coverage_gate() -> None:
    obligations = [
        {
            "id": "O1",
            "quoted_span": "Can you cover Petaluma?",
            "kind": "request",
            "obligation": "decline_with_reason",
            "semantic_reading": "decline offer",
        },
        {
            "id": "O2",
            "quoted_span": "Please send your monthly availability list.",
            "kind": "request",
            "obligation": "answer",
            "semantic_reading": "standing monthly availability list",
        },
    ]
    brief = (
        "- O1: answer_in_message (decline offer)\n"
        "- O2: omit_with_reason (per-offer only)"
    )
    draft = "Maria, I can't take those Petaluma shifts. Thanks, Kaywan"
    cov_map, refusal = finalize._obligation_coverage_gate(
        draft,
        obligations,
        selected="draft_v1",
        audit_map=None,
        revision_ran=False,
        architect_brief=brief,
        channel="sms",
    )
    by_id = {str(r.get("id")): r for r in cov_map}
    assert by_id["O1"].get("status") == "declined_with_reason"
    assert by_id["O2"].get("reply_mode") == "omit_with_reason"
    assert refusal is None


def test_decline_phrasing_natural_sms_covers_obligation() -> None:
    draft = "Maria, I won't be able to cover the Petaluma dates you sent. Thank you, Kaywan"
    cov = _obligation.coverage_map_for_draft(
        draft,
        [
            {
                "id": "O1",
                "quoted_span": "Can you cover Petaluma?",
                "kind": "request",
                "obligation": "decline_with_reason",
                "semantic_reading": "cover Petaluma shifts",
            }
        ],
    )
    assert cov[0]["status"] == "declined_with_reason"


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
    assert active == ["drafter_anthropic"]
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
        "My hourly rate is $200. I can start on Monday.\n\nThanks,\nPat"
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


def _finalize_outputs(
    draft_text: str,
    obligations: list[dict[str, Any]],
    *,
    channel: str = "email",
) -> dict[str, Any]:
    outputs = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "channel": channel,
                "pin_ledger": [{"pin_id": "P1", "attested": True}],
                "writer_seat": "local",
                "reviewer_seat": "local",
                "output": "envelope",
            }
        ),
        "source_obligations": _Out({"obligations": obligations}),
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
    return payload


def test_s1_audit_cannot_override_missing_act_outside_message() -> None:
    src = _run_source(_MERIDIAL)
    act_id = next(
        o["id"]
        for o in src["obligations"]
        if o.get("obligation") == "act_outside_message"
    )
    draft = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "Thanks for reaching out. I'd love to chat more about the team.\n\n"
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
        "obligation_coverage": _Out(
            {
                "coverage_map": [
                    {
                        "id": act_id,
                        "kind": "call_to_action",
                        "obligation": "act_outside_message",
                        "reply_mode": "act_then_confirm",
                        "status": "addressed",
                        "covering_sentence": "I'd love to chat more about the team.",
                    }
                ]
            }
        ),
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
    assert payload.get("refused") == "obligation_coverage_failed"


def test_s1_audit_declined_with_reason_cannot_override_missing_without_decline_phrase() -> None:
    src = _run_source(_MERIDIAL)
    act_id = next(
        o["id"]
        for o in src["obligations"]
        if o.get("obligation") == "act_outside_message"
    )
    draft = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "Thanks for reaching out. I'd love to chat more about the team.\n\n"
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
        "obligation_coverage": _Out(
            {
                "coverage_map": [
                    {
                        "id": act_id,
                        "kind": "call_to_action",
                        "obligation": "act_outside_message",
                        "reply_mode": "omit_with_reason",
                        "status": "declined_with_reason",
                        "covering_sentence": "I'd love to chat more about the team.",
                    }
                ]
            }
        ),
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
    assert payload.get("refused") == "obligation_coverage_failed"


def test_s1_audit_declined_with_reason_passes_when_sentence_explicitly_declines() -> None:
    act_id = "O-act"
    obligations = [
        {
            "id": act_id,
            "quoted_span": "apply here at https://meridial.ai/apply",
            "kind": "call_to_action",
            "obligation": "act_outside_message",
            "semantic_reading": "apply",
        }
    ]
    draft = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "Thanks for reaching out. I will not be applying for this role.\n\n"
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
        "source_obligations": _Out({"obligations": obligations}),
        "obligation_coverage": _Out(
            {
                "coverage_map": [
                    {
                        "id": act_id,
                        "kind": "call_to_action",
                        "obligation": "act_outside_message",
                        "reply_mode": "omit_with_reason",
                        "status": "declined_with_reason",
                        "covering_sentence": "I will not be applying for this role.",
                    }
                ]
            }
        ),
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
    assert payload.get("refused") is None
    cov_map = payload.get("coverage_map") or []
    act_row = next(row for row in cov_map if row.get("id") == act_id)
    assert act_row["status"] == "declined_with_reason"


def _meridial_act_obligation() -> list[dict[str, Any]]:
    src = _run_source(_MERIDIAL)
    return _obligation.actionable_obligations(
        [o for o in src["obligations"] if o.get("obligation") == "act_outside_message"]
    )


def test_action_confirm_rejects_unrelated_booked_completed_phrases() -> None:
    obligations = _meridial_act_obligation()
    negatives = [
        "I'm fully booked on Monday and the project was completed at Google.",
        "I completed my PhD at Stanford in 2019.",
        "I've submitted three papers this year.",
        "I booked a flight to Boston.",
        "I applied to a similar role at another company last year.",
        "I've applied for jobs like this before.",
        "I submitted to NeurIPS this year.",
        "I registered for a conference in May.",
        "I completed in record time.",
        "I submitted on time for every deadline.",
        "I completed at the time.",
        "I applied for the role of senior engineer at Acme in 2022.",
    ]
    for sentence in negatives:
        assert _obligation._ACTION_CONFIRM.search(sentence) is None
        draft = (
            "To: recruiter@meridial.ai\n"
            "Subject: Re: role\n\n"
            f"{sentence}\n\n"
            "Best,\nPat"
        )
        cov = _obligation.coverage_map_for_draft(draft, obligations)
        assert cov[0]["status"] == "missing", sentence
    positives = [
        "I applied through https://meridial.ai/apply today.",
        "I submitted my application through https://meridial.ai/apply today.",
        "I will apply through the link today.",
    ]
    for sentence in positives:
        assert _obligation._ACTION_CONFIRM.search(sentence) is not None, sentence
        draft = (
            "To: recruiter@meridial.ai\n"
            "Subject: Re: role\n\n"
            f"{sentence}\n\n"
            "Best,\nPat"
        )
        cov = _obligation.coverage_map_for_draft(draft, obligations)
        assert cov[0]["status"] == "addressed", sentence


def test_curly_apostrophe_action_and_decline_matching() -> None:
    obligations = _meridial_act_obligation()
    applied = "I\u2019ve applied through the link today."
    draft_applied = (
        "To: recruiter@meridial.ai\nSubject: Re: role\n\n"
        f"{applied}\n\nBest,\nPat"
    )
    pin_ledger = [
        {
            "pin_id": "P1",
            "attested": True,
            "excerpt": "I\u2019ve applied for the role.",
        }
    ]
    cov_applied = _obligation.coverage_map_for_draft(
        draft_applied, obligations, pin_ledger=pin_ledger
    )
    assert cov_applied[0]["status"] == "addressed"
    decline_row = {
        "id": "O-decline",
        "quoted_span": "apply for this role",
        "kind": "request",
        "obligation": "answer_in_message",
    }
    decline_sentence = "I won\u2019t be applying for this role."
    status, covering = _obligation.verify_obligation(
        f"To: x\nSubject: y\n\n{decline_sentence}\n\nBest,\nPat",
        decline_row,
    )
    assert status == "declined_with_reason"
    assert covering == decline_sentence
    assert not _obligation._sentence_declines("I can\u2019t wait to talk.")
    assert _obligation._sentence_declines("I can\u2019t start Monday.")
    assert _obligation._CTA_DECLINE_STEM.search(
        "I cannot share details about my current role."
    ) is None


def test_act_outside_message_declined_availability_passes_coverage_gate() -> None:
    row = {
        "id": "O1",
        "quoted_span": (
            "please send over your standing weekly availability and your open "
            "dates for next month's calendar so we can update your file"
        ),
        "kind": "request",
        "obligation": "act_outside_message",
    }
    draft = (
        "To: Amanda@example.com\n"
        "Subject: Re: availability\n\n"
        "I don't have standing availability on any day of the week. "
        "I also can't provide a monthly open calendar.\n\n"
        "Best,\nPat"
    )
    status, covering = _obligation.verify_obligation(draft, row, pin_ledger=[])
    assert status == "declined_with_reason"
    assert covering
    cov_map, refusal = finalize._obligation_coverage_gate(
        draft,
        [row],
        selected="draft_v1",
        audit_map=None,
        revision_ran=False,
        architect_brief="",
        channel="email",
    )
    assert refusal is None
    assert cov_map[0]["status"] == "declined_with_reason"


def test_body_sentences_keep_terminal_punctuation_lines() -> None:
    draft = (
        "To: client@example.com\n"
        "Subject: Re: timing\n\n"
        "Tuesday.\n"
        "Yes, Monday works.\n\n"
        "Best,\nPat"
    )
    sentences = _obligation._sentences(draft)
    assert "Tuesday." in sentences
    assert "Yes, Monday works." in sentences


def test_s1_meridial_chat_without_apply_action_refuses() -> None:
    src = _run_source(_MERIDIAL)
    draft = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "Thanks for reaching out. I'd love to chat more about the team.\n\n"
        "Best,\nPat"
    )
    cov = _obligation.coverage_map_for_draft(
        draft, _obligation.actionable_obligations(src["obligations"])
    )
    apply_row = next(
        (row for row in cov if row.get("obligation") == "act_outside_message"),
        None,
    )
    assert apply_row is not None
    assert apply_row["status"] == "missing"
    payload = _finalize_outputs(draft, src["obligations"])
    assert payload.get("refused") == "obligation_coverage_failed"
    assert payload.get("status") == "refused"


def test_b1_audit_decline_on_other_ask_cannot_cover_unrelated_missing_ask() -> None:
    inbound = (
        "What is your hourly rate? "
        "Can you start on Monday? "
        "Please send your portfolio link."
    )
    obligations = source_obligations.fallback_obligations(inbound)
    portfolio_id = next(
        o["id"]
        for o in obligations
        if "portfolio" in str(o.get("quoted_span") or "").lower()
    )
    draft = (
        "To: client@example.com\n"
        "Subject: Re: your questions\n\n"
        "My hourly rate is $200. I can't start on Monday.\n\n"
        "Thanks,\nPat"
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
        "obligation_coverage": _Out(
            {
                "coverage_map": [
                    {
                        "id": portfolio_id,
                        "kind": "request",
                        "obligation": "answer_in_message",
                        "reply_mode": "omit_with_reason",
                        "status": "declined_with_reason",
                        "covering_sentence": "I can't start on Monday.",
                    }
                ]
            }
        ),
        "draft": _Out(
            {"ok": True, "draft": draft},
            model_id="anthropic/claude-sonnet-5-5",
        ),
        "independence": _Out(
            {
                "refused": None,
                "writer_model": "anthropic/claude-sonnet-5-5",
                "reviewer_model": "qwen3-5-9b-q8-0-131072",
            }
        ),
        "fact_ceiling_audit": _Out(
            {
                "verdict": "ship",
                "findings": [],
                "reviewer_model": "qwen3-5-9b-q8-0-131072",
            }
        ),
        "provenance_check": _Out({"violations": []}),
        "prose_rhythm_pass": _Out(None, raw=draft),
        "fact_ceiling_final": _Out({"violations": []}),
    }
    payload = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    assert payload.get("refused") == "obligation_coverage_failed"
    unresolved_ids = [
        u["claim_id"] for u in (payload.get("ship_gate") or {}).get("unresolved") or []
    ]
    assert unresolved_ids == [portfolio_id]


def test_s2_three_ask_portfolio_missing_not_declined() -> None:
    inbound = (
        "What is your hourly rate? "
        "Can you start on Monday? "
        "Please send your portfolio link."
    )
    obligations = source_obligations.fallback_obligations(inbound)
    draft = (
        "To: client@example.com\n"
        "Subject: Re: your questions\n\n"
        "My hourly rate is $200. I can start on Monday. "
        "I can't wait to hear from you.\n\nThanks,\nPat"
    )
    cov = _obligation.coverage_map_for_draft(
        draft, _obligation.actionable_obligations(obligations)
    )
    portfolio_rows = [
        row
        for row in cov
        if "portfolio" in str(
            next(
                (
                    o.get("quoted_span")
                    for o in obligations
                    if o.get("id") == row.get("id")
                ),
                "",
            )
        ).lower()
    ]
    assert portfolio_rows
    assert portfolio_rows[0]["status"] == "missing"
    assert portfolio_rows[0]["status"] != "declined_with_reason"
    payload = _finalize_outputs(draft, obligations)
    assert payload.get("refused") == "obligation_coverage_failed"


def test_s3_shared_stopwords_do_not_count_as_answer() -> None:
    obligations = source_obligations.fallback_obligations(
        "Can you send your portfolio link?"
    )
    draft = (
        "To: client@example.com\n"
        "Subject: Re: question\n\n"
        "Can you share your availability?\n\nThanks,\nPat"
    )
    cov = _obligation.coverage_map_for_draft(
        draft, _obligation.actionable_obligations(obligations)
    )
    assert cov[0]["status"] == "missing"
    payload = _finalize_outputs(draft, obligations)
    assert payload.get("refused") == "obligation_coverage_failed"


def test_act_outside_message_unattested_past_claim_refuses_and_degrades_reply_mode_to_ask_back() -> None:
    src = _run_source(_MERIDIAL)
    obligations = _obligation.actionable_obligations(src["obligations"])
    pin_ledger = [
        {"pin_id": "P1", "attested": True, "excerpt": "I live in the South Bay."}
    ]
    past_draft = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "I submitted my application through https://meridial.ai/apply today.\n\n"
        "Best,\nPat"
    )
    cov_past = _obligation.coverage_map_for_draft(
        past_draft, obligations, pin_ledger=pin_ledger
    )
    act_row = next(r for r in cov_past if r.get("obligation") == "act_outside_message")
    assert act_row["status"] == "missing"
    assert act_row["reply_mode"] == "ask_back"
    outputs_past = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "channel": "email",
                "pin_ledger": pin_ledger,
                "writer_seat": "local",
                "reviewer_seat": "local",
                "output": "envelope",
            }
        ),
        "source_obligations": _Out({"obligations": src["obligations"]}),
        "draft": _Out({"ok": True, "draft": past_draft}),
        "independence": _Out(
            {
                "refused": None,
                "writer_model": "anthropic/claude-sonnet-5-5",
                "reviewer_model": "qwen3-5-9b-q8-0-131072",
            }
        ),
        "fact_ceiling_audit": _Out({"verdict": "ship", "findings": []}),
        "provenance_check": _Out({"violations": []}),
        "prose_rhythm_pass": _Out(None, raw=past_draft),
        "fact_ceiling_final": _Out({"violations": []}),
    }
    finalized = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs_past))
    ).json
    assert finalized is not None
    assert finalized.get("refused") == "obligation_coverage_failed"

    intent_draft = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "Thank you for the AI Trust and Safety outreach. "
        "I plan to apply through https://meridial.ai/apply and would love to "
        "know the next steps.\n\n"
        "Best,\nPat"
    )
    cov_intent = _obligation.coverage_map_for_draft(
        intent_draft, obligations, pin_ledger=pin_ledger
    )
    act_intent = next(
        r for r in cov_intent if r.get("obligation") == "act_outside_message"
    )
    assert act_intent["status"] == "addressed"
    assert act_intent["reply_mode"] == "ask_back"
    outputs_intent = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "channel": "email",
                "pin_ledger": pin_ledger,
                "writer_seat": "local",
                "reviewer_seat": "local",
                "output": "envelope",
            }
        ),
        "source_obligations": _Out({"obligations": src["obligations"]}),
        "draft": _Out({"ok": True, "draft": intent_draft}),
        "independence": _Out(
            {
                "refused": None,
                "writer_model": "anthropic/claude-sonnet-5-5",
                "reviewer_model": "qwen3-5-9b-q8-0-131072",
            }
        ),
        "fact_ceiling_audit": _Out({"verdict": "ship", "findings": []}),
        "provenance_check": _Out({"violations": []}),
        "prose_rhythm_pass": _Out(None, raw=intent_draft),
        "fact_ceiling_final": _Out({"violations": []}),
    }
    payload_intent = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs_intent))
    ).json
    assert payload_intent is not None
    assert payload_intent.get("refused") is None


def test_act_outside_message_attested_past_claim_allows_act_then_confirm() -> None:
    src = _run_source(_MERIDIAL)
    obligations = _obligation.actionable_obligations(src["obligations"])
    pin_ledger = [
        {
            "pin_id": "P1",
            "attested": True,
            "excerpt": (
                "I submitted my application through https://meridial.ai/apply today."
            ),
        }
    ]
    draft = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "Thank you for the AI Trust and Safety outreach. "
        "I submitted my application through https://meridial.ai/apply today.\n\n"
        "Best,\nPat"
    )
    cov = _obligation.coverage_map_for_draft(
        draft, obligations, pin_ledger=pin_ledger
    )
    act_row = next(r for r in cov if r.get("obligation") == "act_outside_message")
    assert act_row["status"] == "addressed"
    assert act_row["reply_mode"] == "act_then_confirm"
    outputs = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "channel": "email",
                "pin_ledger": pin_ledger,
                "writer_seat": "local",
                "reviewer_seat": "local",
                "output": "envelope",
            }
        ),
        "source_obligations": _Out({"obligations": src["obligations"]}),
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
    assert payload.get("refused") is None


def test_act_outside_b1_mixed_sentence_unattested_past_fails() -> None:
    obligations = _meridial_act_obligation()
    pin_ledger = [
        {"pin_id": "P1", "attested": True, "excerpt": "I live in the South Bay."}
    ]
    mixed_sentences = [
        (
            "I submitted my application through the portal, so no need to apply "
            "through the link."
        ),
        (
            "I applied through the portal yesterday and will apply through the "
            "link for the second role."
        ),
    ]
    for sentence in mixed_sentences:
        draft = (
            "To: recruiter@meridial.ai\nSubject: Re: role\n\n"
            f"{sentence}\n\nBest,\nPat"
        )
        cov = _obligation.coverage_map_for_draft(
            draft, obligations, pin_ledger=pin_ledger
        )
        assert cov[0]["status"] == "missing", sentence


def test_act_outside_b2_body_wide_unattested_past_fails_finalize() -> None:
    src = _run_source(_MERIDIAL)
    obligations = _obligation.actionable_obligations(src["obligations"])
    pin_ledger = [
        {"pin_id": "P1", "attested": True, "excerpt": "I live in the South Bay."}
    ]
    draft = (
        "To: recruiter@meridial.ai\n"
        "Subject: AI Trust and Safety application\n\n"
        "Thank you for the AI Trust and Safety outreach. "
        "I applied through the link. "
        "I'll apply through the link for the second role.\n\n"
        "Best,\nPat"
    )
    cov = _obligation.coverage_map_for_draft(
        draft, obligations, pin_ledger=pin_ledger
    )
    act_row = next(r for r in cov if r.get("obligation") == "act_outside_message")
    assert act_row["status"] == "missing"
    outputs = {
        "assemble": _Out(
            {
                "ok": True,
                "refused": None,
                "channel": "email",
                "pin_ledger": pin_ledger,
                "writer_seat": "local",
                "reviewer_seat": "local",
                "output": "envelope",
            }
        ),
        "source_obligations": _Out({"obligations": src["obligations"]}),
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
    assert payload.get("refused") == "obligation_coverage_failed"


def test_act_outside_b3_negated_pin_excerpt_not_attested_act() -> None:
    negated_ledgers = [
        [{"pin_id": "P1", "attested": True, "excerpt": "I haven't applied through the link."}],
        [
            {
                "pin_id": "P1",
                "attested": True,
                "excerpt": "I have not applied through the portal yet.",
            }
        ],
        [{"pin_id": "P1", "attested": True, "excerpt": "I never applied through the portal."}],
    ]
    obligations = _meridial_act_obligation()
    draft = (
        "To: recruiter@meridial.ai\nSubject: Re: role\n\n"
        "I applied through the link.\n\nBest,\nPat"
    )
    for pin_ledger in negated_ledgers:
        assert _obligation.has_attested_external_act(pin_ledger) is False
        cov = _obligation.coverage_map_for_draft(
            draft, obligations, pin_ledger=pin_ledger
        )
        assert cov[0]["status"] == "missing"
    trailing_neg = [
        {
            "pin_id": "P1",
            "attested": True,
            "excerpt": "I was going to say I applied through the link, but I haven't.",
        }
    ]
    assert _obligation.has_attested_external_act(trailing_neg) is False


def test_act_outside_b4_clause_openers_with_unattested_past_claim_fail() -> None:
    obligations = _meridial_act_obligation()
    pin_ledger: list[dict[str, Any]] = []
    suffix = " I'll apply through the link for the second role."
    bodies = [
        "Not to worry, I applied through the portal." + suffix,
        (
            "No need to resend anything; I submitted my application through "
            "the portal." + suffix
        ),
        "I didn't hesitate - I applied through the portal." + suffix,
        "Not a problem: I submitted my application through the portal." + suffix,
        (
            "Without delay I applied through the portal, and I will apply "
            "through the link too."
        ),
    ]
    for body in bodies:
        draft = (
            "To: recruiter@meridial.ai\nSubject: Re: role\n\n"
            f"{body}\n\nBest,\nPat"
        )
        cov = _obligation.coverage_map_for_draft(
            draft, obligations, pin_ledger=pin_ledger
        )
        assert cov[0]["status"] == "missing", body


def test_reply_closing_let_me_know_is_pleasantry() -> None:
    for span in ("Let me know, thanks! -Maria", "Let me know, thanks!"):
        rows = source_obligations.fallback_obligations(span)
        assert len(rows) == 1
        assert rows[0]["kind"] == "pleasantry"
        assert rows[0]["obligation"] == "none"
    substantive = "Please let me know your availability for next month"
    sub = source_obligations.fallback_obligations(substantive)[0]
    assert sub["obligation"] == "answer"
    assert sub["kind"] != "pleasantry"
    parsed = source_obligations._parse_model_obligations(
        json.dumps(
            {
                "obligations": [
                    {
                        "id": "O1",
                        "quoted_span": "Let me know, thanks!",
                        "kind": "request",
                        "obligation": "answer",
                    }
                ]
            }
        ),
        "Let me know, thanks!",
    )
    assert parsed is not None
    assert parsed[0]["kind"] == "pleasantry"
    assert parsed[0]["obligation"] == "none"


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
