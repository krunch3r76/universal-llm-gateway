"""Offline checks for local persona author and writer_local prompts."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from systems.pipeline.core.conditions import ConditionEvaluator
from systems.pipeline.core.pipeline_config import PipelineSpec
from systems.pipeline.core.prompts import PromptBuilder

_PKG = "writing_v1_handlers"
_V1 = Path(__file__).resolve().parents[1]
if _PKG not in sys.modules:
    _HANDLERS = _V1 / "handlers"
    _spec = importlib.util.spec_from_file_location(
        _PKG,
        _HANDLERS / "__init__.py",
        submodule_search_locations=[str(_HANDLERS)],
    )
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _mod
    _spec.loader.exec_module(_mod)

local_persona = importlib.import_module("writing_v1_handlers.local_persona_author")
prompt_store = importlib.import_module("writing_v1_handlers.prompt_store")
semantic_judge = importlib.import_module("writing_v1_handlers.semantic_judge")
judge_events = importlib.import_module("writing_pipeline.events")

pytestmark = pytest.mark.offline

_WRITER_YAML_SHA256 = "b58cb267182c01c260be856aa3455c33c9124c746494c5bbe917cc4dc23931f9"

_CORRESPONDENCE_INTAKE = {
    "mode": "standard",
    "channel": "email",
    "recipient": "AABHearing@cob.sccgov.org",
    "signer": "Kaywan Joseph Mansubi",
    "objective": (
        "Short cover email about Assessment Appeals 25.7489 and hearing October 27 2026."
    ),
    "verbatim_notes": [
        "Kaywan Joseph Mansubi submitted exhibits for Assessment Appeals 25.7489.",
        "Hearing Tuesday, October 27, 2026.",
    ],
    "standing_limits": "Plain and brief. Use only facts in verbatim_notes.",
}

_BRIEF_BLOCK = (
    "<brief>Kaywan Joseph Mansubi channel email "
    "Assessment Appeals 25.7489 October 27 2026</brief>"
)
_DOCS_BLOCK = (
    "<documents><pin id='P1'>Kaywan Joseph Mansubi hearing "
    "Tuesday October 27 2026</pin></documents>"
)
_ARCH_BLOCK = (
    "<architect_brief>lead_pin_id: P1 Assessment Appeals 25.7489</architect_brief>"
)


def _blocks_kwargs() -> dict[str, str]:
    return {
        "brief_block": _BRIEF_BLOCK,
        "documents_block": _DOCS_BLOCK,
        "architect_brief_block": _ARCH_BLOCK,
    }


def _judge_kinds(kinds: dict[str, tuple[str, str]]) -> Any:
    async def _caller(_fenced: str, candidates: list[local_persona.Candidate]) -> str:
        rows = []
        for cand in candidates:
            kind, basis = kinds.get(cand.token, ("ordinary_language", ""))
            rows.append(
                {
                    "token": cand.token,
                    "basis": basis,
                    "reading": "Unit-test reading.",
                    "kind": kind,
                }
            )
        return json.dumps({"judgments": rows})

    return _caller


def _judge_invented(tokens: list[str]) -> Any:
    return _judge_kinds({t: ("new_entity_or_fact", "") for t in tokens})


def _judge_raw(content: str) -> Any:
    async def _caller(_fenced: str, _candidates: list[local_persona.Candidate]) -> str:
        return content

    return _caller


def _judge_raises(exc: BaseException) -> Any:
    async def _caller(_fenced: str, _candidates: list[local_persona.Candidate]) -> str:
        raise exc

    return _caller


def _run_validate(
    prose: str,
    *,
    judge_caller: Any = None,
    **blocks: str,
) -> local_persona.PersonaValidationResult:
    kw = blocks or _blocks_kwargs()
    return asyncio.run(
        local_persona.validate_persona_prose(
            prose,
            brief_block=kw["brief_block"],
            documents_block=kw["documents_block"],
            architect_brief_block=kw["architect_brief_block"],
            judge_caller=judge_caller,
        )
    )


class _Out:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.json = payload


class _Ctx:
    def __init__(self, outputs: dict[str, _Out]) -> None:
        self.outputs = outputs


def _correspondence_ctx() -> _Ctx:
    return _Ctx(
        {
            "assemble": _Out(
                {
                    "refused": None,
                    "intake": dict(_CORRESPONDENCE_INTAKE),
                    "brief_block": _BRIEF_BLOCK,
                    "documents_block": _DOCS_BLOCK,
                }
            ),
            "prompt_architect": _Out({"architect_brief_block": _ARCH_BLOCK}),
        }
    )


def test_writer_local_prompt_wiring() -> None:
    row = prompt_store.load_prompts()["writer_local"]
    assert row["system_prompt"] == "{persona_prose}"
    rendered = PromptBuilder().render(
        row["template"],
        {
            "documents": "<documents>pin text</documents>",
            "channel": "email",
        },
    )
    assert "<documents>" in rendered
    assert "Channel: email" in rendered
    assert rendered.strip().endswith("Write this message in your own voice.")
    assert "gate" not in rendered.lower()


def test_validate_accepts_qwen_register_persona() -> None:
    prose = (
        "You are Kaywan Joseph Mansubi writing to the appeals board. "
        "The hearing on October 27 2026 is already on your calendar. "
        "When you sign off, you keep the tone plain and brief."
    )
    verdict = _run_validate(
        prose, judge_caller=_judge_raises(AssertionError("no judge"))
    )
    assert verdict.ok is True
    assert verdict.text == prose
    assert verdict.judge_called is False


def test_persona_candidates_remember_sentence_start() -> None:
    prose = "You keep receipts. Remember to cite exhibits."
    blocks = _blocks_kwargs()
    allowed = local_persona._persona_allowed_tokens(
        brief_block=blocks["brief_block"],
        documents_block=blocks["documents_block"],
        architect_brief_block=blocks["architect_brief_block"],
    )
    blocks_words = local_persona._blocks_word_set("\n".join(blocks.values()).lower())
    candidates = local_persona._persona_candidates(
        prose, allowed=allowed, blocks_words=blocks_words
    )
    assert [row.token for row in candidates] == ["Remember"]
    assert candidates[0].form == "capitalized_word"
    assert candidates[0].position == "sentence_start"
    assert any("Remember" in ctx for ctx in candidates[0].contexts)


def test_validate_no_candidates_skips_judge() -> None:
    prose = (
        "You are Kaywan Joseph Mansubi writing to the appeals board. "
        "The hearing on October 27 2026 is already on your calendar."
    )
    verdict = _run_validate(
        prose, judge_caller=_judge_raises(AssertionError("no judge"))
    )
    assert verdict.ok is True
    assert verdict.judge_called is False


def test_validate_judge_empty_invented_accepts_candidates() -> None:
    prose = "You keep receipts attached. Conclude the message with thanks."
    verdict = _run_validate(prose, judge_caller=_judge_kinds({}))
    assert verdict.ok is True
    assert verdict.judge_called is True
    assert verdict.flagged_candidates


def test_validate_judge_invented_zaphod_refuses() -> None:
    prose = "You write plainly with Zaphod on every revision."
    verdict = _run_validate(prose, judge_caller=_judge_invented(["Zaphod"]))
    assert verdict.ok is False
    assert verdict.refused == "persona_validation_failed"
    assert verdict.judged_invented == ["Zaphod"]


def test_validate_judge_exception_unavailable() -> None:
    verdict = _run_validate(
        "You met Marcus downtown.",
        judge_caller=_judge_raises(RuntimeError("transport_down")),
    )
    assert verdict.ok is False
    assert verdict.refused == "persona_judge_unavailable"
    assert verdict.judge_called is True


def test_validate_judge_bad_json_invalid() -> None:
    verdict = _run_validate(
        "You met Marcus downtown.",
        judge_caller=_judge_raw("not-json"),
    )
    assert verdict.ok is False
    assert verdict.refused == "persona_judge_invalid"


@pytest.mark.parametrize(
    "prose",
    [
        "You keep receipts attached. Conclude the message with brief thanks.",
        "Maintain the tone plain and brief throughout.",
        "Since the hearing is already set, you keep logistics direct.",
        "You keep receipts on file. Remember to cite exhibit numbers.",
        "You shift to a calmer register. Offer to follow up by email.",
        "You sign as Kaywan Joseph Mansubi. Keep it brief on logistics.",
    ],
)
def test_validate_accepts_sentence_initial_imperative_operators(prose: str) -> None:
    verdict = _run_validate(prose, judge_caller=_judge_kinds({}))
    assert verdict.ok is True
    assert verdict.judge_called is True


def test_validate_judge_rejects_kay_truncation() -> None:
    verdict = _run_validate(
        "Kay is your contact.", judge_caller=_judge_invented(["Kay"])
    )
    assert verdict.ok is False
    assert verdict.refused == "persona_validation_failed"


def test_validate_judge_rejects_marcus_invented() -> None:
    verdict = _run_validate(
        "Marcus is your contact for this filing.",
        judge_caller=_judge_invented(["Marcus"]),
    )
    assert verdict.ok is False


def test_validate_rejects_dual_capital_name_sentence_initial_and_mid() -> None:
    prose = (
        "You already emailed Marcus about the filing. "
        "Marcus is still your point of contact on exhibits."
    )
    verdict = _run_validate(prose, judge_caller=_judge_invented(["Marcus"]))
    assert verdict.ok is False


def test_validate_number_candidate_judged_invented() -> None:
    pat_blocks = {
        "brief_block": "<brief>Pat signer</brief>",
        "documents_block": "<documents>Pat filed on Monday.</documents>",
        "architect_brief_block": "<architect_brief>lead_pin_id P1</architect_brief>",
    }
    verdict = _run_validate(
        "Pat filed on Monday 99 times.",
        judge_caller=_judge_invented(["99"]),
        **pat_blocks,
    )
    assert verdict.ok is False
    assert verdict.judged_invented == ["99"]
    assert verdict.judge_called is True


def test_validate_date_reformat_grounded_basis_accepts() -> None:
    verdict = _run_validate(
        "You filed on 10/27/2026.",
        judge_caller=_judge_kinds({"10": ("restates_attested", "October 27 2026")}),
    )
    assert verdict.ok is True


def test_validate_date_reformat_ungrounded_basis_refuses() -> None:
    verdict = _run_validate(
        "You filed on 10/27/2026.",
        judge_caller=_judge_kinds({"10": ("restates_attested", "Oct 27")}),
    )
    assert verdict.ok is False
    assert verdict.judged_invented == ["10"]


def test_basis_vacuous_stopword_basis_treated_invented() -> None:
    for basis in ("the", "and the"):
        verdict = _run_validate(
            "You met Marcus downtown.",
            judge_caller=_judge_kinds({"Marcus": ("restates_attested", basis)}),
        )
        assert verdict.ok is False
        assert verdict.judged_invented == ["Marcus"]


def test_basis_grounded_after_markup_stripped() -> None:
    glued = {
        "brief_block": "<brief>Tuesday October 27 2026</brief>",
        "documents_block": "<documents>P1</documents>",
        "architect_brief_block": "<architect_brief>P1</architect_brief>",
    }
    verdict = _run_validate(
        "You filed on 10/27/2026.",
        judge_caller=_judge_kinds({"10": ("restates_attested", "October 27 2026")}),
        **glued,
    )
    assert verdict.ok is True


def test_basis_empty_on_restates_treated_invented() -> None:
    verdict = _run_validate(
        "You met Marcus downtown.",
        judge_caller=_judge_kinds({"Marcus": ("restates_attested", "")}),
    )
    assert verdict.ok is False
    assert verdict.judged_invented == ["Marcus"]


def test_validate_judge_truncation_maps_unavailable() -> None:
    from systems.pipeline.core.dag import ResponseTruncatedError

    err = ResponseTruncatedError("persona_judge", 64, 64, "{}")
    verdict = _run_validate(
        "You met Marcus downtown.",
        judge_caller=_judge_raises(err),
    )
    assert verdict.refused == "persona_judge_unavailable"


def test_validate_mixed_imperative_and_name_partial_invented() -> None:
    prose = "Conclude the note. Marcus is your contact."
    verdict = _run_validate(
        prose,
        judge_caller=_judge_kinds(
            {
                "Conclude": ("ordinary_language", ""),
                "Marcus": ("new_entity_or_fact", ""),
            }
        ),
    )
    assert verdict.ok is False
    assert verdict.flagged_candidates == ["Conclude", "Marcus"]
    assert verdict.judged_invented == ["Marcus"]


@pytest.mark.parametrize(
    "prose",
    [
        "You write plainly.\n- No lists",
        "You write plainly.\n1. Numbered",
    ],
)
def test_validate_rejects_bad_persona(prose: str) -> None:
    pat_blocks = {
        "brief_block": "<brief>Pat signer</brief>",
        "documents_block": "<documents>Pat filed on Monday.</documents>",
        "architect_brief_block": "<architect_brief>lead_pin_id P1</architect_brief>",
    }
    verdict = _run_validate(
        prose,
        judge_caller=_judge_raises(AssertionError("no judge")),
        **pat_blocks,
    )
    assert verdict.ok is False
    assert verdict.judge_called is False


def test_validate_candidates_without_judge_caller_unavailable() -> None:
    verdict = _run_validate("You met Marcus downtown.", judge_caller=None)
    assert verdict.ok is False
    assert verdict.refused == "persona_judge_unavailable"


def test_validate_thinking_preamble_fails_without_judge() -> None:
    verdict = _run_validate(
        "<think>x</think> You are Kaywan.",
        judge_caller=_judge_raises(AssertionError("no judge")),
    )
    assert verdict.ok is False
    assert verdict.judge_called is False


def test_validate_judge_strips_thinking_before_parse() -> None:
    body = json.dumps(
        {
            "judgments": [
                {
                    "token": "Marcus",
                    "basis": "",
                    "reading": "r",
                    "kind": "new_entity_or_fact",
                }
            ]
        }
    )
    verdict = _run_validate(
        "You met Marcus downtown.",
        judge_caller=_judge_raw(f"<think>t</think>{body}"),
    )
    assert verdict.refused == "persona_validation_failed"


@pytest.mark.parametrize(
    "payload",
    [
        '{"judgments": []}',
        '{"judgments": [{"token": "Marcus", "basis": "", "reading": "r", "kind": "new_entity_or_fact"}, {"token": "Marcus", "basis": "", "reading": "r", "kind": "new_entity_or_fact"}]}',
        '{"judgments": [{"token": "Zaphod", "basis": "", "reading": "r", "kind": "new_entity_or_fact"}]}',
        '{"judgments": [{"token": "Marcus", "basis": "", "reading": "r", "kind": "bogus"}]}',
    ],
)
def test_validate_judge_invalid_coverage(payload: str) -> None:
    verdict = _run_validate(
        "You met Marcus downtown.",
        judge_caller=_judge_raw(payload),
    )
    assert verdict.refused == "persona_judge_invalid"


def test_persona_judge_schema_pins_rows_and_enums() -> None:
    judge_schema = importlib.import_module("writing_v1_handlers.judge_schema")
    schema = judge_schema.persona_judge_schema(["Conclude", "10"])
    judgments = schema["properties"]["judgments"]
    assert judgments["minItems"] == judgments["maxItems"] == 2
    props = judgments["items"]["properties"]
    assert props["token"]["enum"] == ["Conclude", "10"]
    assert list(props.keys()) == ["token", "basis", "reading", "kind"]


def test_persona_judge_prompt_placeholders() -> None:
    row = prompt_store.load_prompts()["persona_invented_judge"]
    template = str(row["template"])
    placeholders = PromptBuilder().get_placeholders(template)
    assert placeholders == {
        "brief",
        "documents",
        "architect_brief",
        "persona_prose",
        "flagged_candidates",
        "candidate_count",
    }
    PromptBuilder().render(
        template,
        {
            "brief": "<brief>x</brief>",
            "documents": "<documents>x</documents>",
            "architect_brief": "<architect_brief>x</architect_brief>",
            "persona_prose": "prose",
            "flagged_candidates": "[]",
            "candidate_count": "0",
        },
    )


def test_validate_too_many_candidates_refuses_without_judge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_persona, "_MAX_PERSONA_CANDIDATES", 1)
    verdict = _run_validate(
        "Alpha Beta are named.",
        judge_caller=_judge_raises(AssertionError("no judge")),
    )
    assert verdict.refused == "persona_validation_failed"
    assert verdict.detail == "too_many_candidates"
    assert verdict.judge_called is False


def test_persona_author_fail_closed_skips_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[Any] = []

    def _capture(_self: object, _ctx: object, event: Any) -> None:
        published.append(event)

    async def _bad_model(
        _self: object,
        _model_id: str,
        _prompt: str,
        _step: object,
        _context: object,
        **_: Any,
    ) -> object:
        return type("Result", (), {"content": "I am Zaphod Beetlebrox on Mars."})()

    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_publish_bus_event",
        _capture,
    )
    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_call_model",
        _bad_model,
    )
    handler = local_persona.WritingLocalPersonaAuthorHandler(
        judge_caller=_judge_invented(["Zaphod", "Beetlebrox", "Mars"])
    )
    payload = asyncio.run(handler.execute(None, _correspondence_ctx())).json
    assert payload.get("ok") is False
    assert payload.get("refused") == "persona_validation_failed"
    assert payload.get("persona_refused") == "persona_validation_failed"
    assert payload.get("persona_rejected_text")
    assert "persona_prose" not in payload
    assert "_skipped" not in payload
    assert published and published[0].signal == "writing.persona.refused"
    assert published[0].role == "observation"
    assert payload.get("persona_judgments")
    assert published[0].payload.get("persona_judgments")


def test_too_many_candidates_event_carries_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[Any] = []

    def _capture(_self: object, _ctx: object, event: Any) -> None:
        published.append(event)

    async def _good_model(
        _self: object,
        _model_id: str,
        _prompt: str,
        _step: object,
        _context: object,
        **_: Any,
    ) -> object:
        return type("Result", (), {"content": "Alpha Beta are here."})()

    monkeypatch.setattr(local_persona, "_MAX_PERSONA_CANDIDATES", 1)
    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_publish_bus_event",
        _capture,
    )
    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_call_model",
        _good_model,
    )
    payload = asyncio.run(
        local_persona.WritingLocalPersonaAuthorHandler(
            judge_caller=_judge_raises(AssertionError("no judge"))
        ).execute(None, _correspondence_ctx())
    ).json
    assert payload.get("detail") == "too_many_candidates"
    assert published[0].payload.get("detail") == "too_many_candidates"


def test_invoke_persona_judge_offline_call_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    models = importlib.import_module("writing_v1_handlers.independence")

    async def _fake_call(
        _self: object,
        model_id: str,
        prompt: str,
        _step: object,
        _context: object,
        **kwargs: Any,
    ) -> object:
        captured["model_id"] = model_id
        captured["prompt"] = prompt
        captured.update(kwargs)
        return type("Result", (), {"content": "{}"})()

    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_call_model",
        _fake_call,
    )
    handler = local_persona.WritingLocalPersonaAuthorHandler()
    blocks = _blocks_kwargs()
    prose = "You keep <brief>note</brief> receipts."
    candidates = [
        local_persona.Candidate(
            token="Remember",
            form="capitalized_word",
            position="sentence_start",
            contexts=["You keep receipts."],
        )
    ]
    asyncio.run(
        handler._invoke_persona_judge(
            None,
            None,
            persona_prose_fenced=local_persona._fence(prose),
            candidates=candidates,
            brief_block=blocks["brief_block"],
            documents_block=blocks["documents_block"],
            architect_brief_block=blocks["architect_brief_block"],
        )
    )
    loaded = models.load_writing_models()
    assert captured["model_id"] == str(loaded.get("persona_judge"))
    assert captured.get("temperature") == 0.0
    assert captured.get("max_tokens") == 64 + 220 * len(candidates)
    assert captured.get("call_label") == "persona_judge"
    assert captured.get("system_prompt")
    schema = captured.get("json_schema") or {}
    enum = schema["properties"]["judgments"]["items"]["properties"]["token"]["enum"]
    assert enum == ["Remember"]
    prompt = str(captured.get("prompt") or "")
    assert blocks["brief_block"] in prompt
    assert blocks["documents_block"] in prompt
    assert blocks["architect_brief_block"] in prompt
    assert "candidate_count" in prompt or "1" in prompt
    assert "&lt;" in prompt or "&lt;brief" in local_persona._fence(prose)


def test_refuse_payload_when_bus_publish_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_self: object, _ctx: object, _event: Any) -> None:
        raise RuntimeError("bus_down")

    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_publish_bus_event",
        _boom,
    )
    handler = local_persona.WritingLocalPersonaAuthorHandler()
    payload = handler._refuse(
        _Ctx({}),
        "persona_validation_failed",
        rejected_text="bad",
    ).json
    assert payload.get("ok") is False
    assert payload.get("persona_refused") == "persona_validation_failed"


def test_persona_author_model_exception_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(
        _self: object,
        _model_id: str,
        _prompt: str,
        _step: object,
        _context: object,
        **_: Any,
    ) -> object:
        raise RuntimeError("transport_down")

    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_call_model",
        _boom,
    )
    payload = asyncio.run(
        local_persona.WritingLocalPersonaAuthorHandler().execute(
            None, _correspondence_ctx()
        )
    ).json
    assert payload.get("ok") is False
    assert payload.get("persona_refused") == "persona_author_failed"


def test_persona_author_missing_prompt_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_persona, "load_prompts", lambda: {})
    payload = asyncio.run(
        local_persona.WritingLocalPersonaAuthorHandler().execute(
            None, _correspondence_ctx()
        )
    ).json
    assert payload.get("persona_refused") == "persona_prompt_missing"


def test_persona_author_success_emits_block(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _good_model(
        _self: object,
        _model_id: str,
        _prompt: str,
        _step: object,
        _context: object,
        **_: Any,
    ) -> object:
        return type(
            "Result",
            (),
            {
                "content": (
                    "You are Kaywan Joseph Mansubi writing about "
                    "Assessment Appeals 25.7489 and the October 27 2026 hearing."
                )
            },
        )()

    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_call_model",
        _good_model,
    )
    handler = local_persona.WritingLocalPersonaAuthorHandler(
        judge_caller=_judge_kinds({})
    )
    payload = asyncio.run(handler.execute(None, _correspondence_ctx())).json
    assert payload.get("ok") is True
    assert payload.get("persona_prose")
    assert "<persona_prose>" in str(payload.get("persona_prose_block"))


def test_writer_yaml_sha256_pinned() -> None:
    digest = hashlib.sha256((_V1 / "prompts" / "writer.yaml").read_bytes()).hexdigest()
    assert digest == _WRITER_YAML_SHA256


def test_fanout_persona_fail_leaves_four_cloud_drafters() -> None:
    from systems.pipeline.core.handlers.step_output import StepOutput

    raw = yaml.safe_load(
        (_V1 / "writer-specialist-v1.yaml").read_text(encoding="utf-8")
    )
    by_name = {step.name: step for step in PipelineSpec(**raw).steps}
    evaluator = ConditionEvaluator()

    def step(data: dict[str, Any]) -> StepOutput:
        return StepOutput(raw="", json=data)

    ctx = {
        "assemble": step({"refused": None, "channel": "email"}),
        "local_persona_author": step(
            {"ok": False, "persona_refused": "persona_validation_failed"}
        ),
    }
    options = {"output": "envelope", "full_drafter_fanout": True}
    names = (
        "drafter_openai",
        "drafter_google",
        "drafter_xai",
        "drafter_local_hermes",
        "drafter_local_ernie",
        "drafter_anthropic",
    )
    active = [
        name
        for name in names
        if evaluator.evaluate(by_name[name].condition, ctx, options)
    ]
    assert active == [
        "drafter_openai",
        "drafter_google",
        "drafter_xai",
        "drafter_anthropic",
    ]


def test_judge_still_sees_cloud_candidates_after_persona_fail() -> None:
    outputs = {
        "drafter_openai": {"raw": "Draft one", "error": None},
        "drafter_google": {"raw": "Draft two", "error": None},
        "drafter_xai": {"raw": "Draft three", "error": None},
        "drafter_anthropic": {"raw": "Draft four", "error": None},
        "drafter_local_hermes": {"error": "skipped"},
        "drafter_local_ernie": {"error": "skipped"},
    }
    found = semantic_judge._eligible_candidates(outputs)
    assert len(found) == 4


def test_persona_refused_event_factory() -> None:
    event = judge_events.WritingPersonaRefused(
        reason="persona_validation_failed",
        flagged_candidates=["Marcus"],
        judged_invented=["Marcus"],
        persona_judgments=[
            {
                "token": "Marcus",
                "kind": "new_entity_or_fact",
                "basis": "",
                "reading": "r",
            }
        ],
        detail="too_many_candidates",
    )
    assert event.signal == "writing.persona.refused"
    assert event.role == "observation"
    assert event.payload["reason"] == "persona_validation_failed"
    assert event.payload["flagged_candidates"] == ["Marcus"]
    assert event.payload["judged_invented"] == ["Marcus"]
    assert event.payload["persona_judgments"]
    assert event.payload["detail"] == "too_many_candidates"


def test_handler_judge_receives_fenced_prose_and_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _good_model(
        _self: object,
        _model_id: str,
        _prompt: str,
        _step: object,
        _context: object,
        **_: Any,
    ) -> object:
        return type(
            "Result",
            (),
            {
                "content": (
                    "You keep <brief>note</brief> receipts. Remember to cite exhibits."
                )
            },
        )()

    async def _judge(fenced: str, candidates: list[local_persona.Candidate]) -> str:
        captured["fenced"] = fenced
        captured["tokens"] = [row.token for row in candidates]
        captured["contexts"] = [row.contexts for row in candidates]
        return json.dumps(
            {
                "judgments": [
                    {
                        "token": row.token,
                        "basis": "",
                        "reading": "r",
                        "kind": "ordinary_language",
                    }
                    for row in candidates
                ]
            }
        )

    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_call_model",
        _good_model,
    )
    handler = local_persona.WritingLocalPersonaAuthorHandler(judge_caller=_judge)
    asyncio.run(handler.execute(None, _correspondence_ctx()))
    assert captured.get("tokens") == ["Remember"]
    assert "&lt;brief" in (captured.get("fenced") or "")
