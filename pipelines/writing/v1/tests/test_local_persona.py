"""Offline checks for local persona author and writer_local prompts."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
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

_WRITER_YAML_SHA256 = (
    "b58cb267182c01c260be856aa3455c33c9124c746494c5bbe917cc4dc23931f9"
)

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
    text, ok = local_persona._validate_persona_prose(prose, **_blocks_kwargs())
    assert ok is True
    assert text == prose


def test_validate_rejects_sentence_initial_marcus() -> None:
    prose = "Marcus is your contact for this filing."
    _, ok = local_persona._validate_persona_prose(prose, **_blocks_kwargs())
    assert ok is False


def test_validate_rejects_invented_mid_sentence_after_stopword_open() -> None:
    prose = "You write plainly. Zaphod agrees with every word."
    _, ok = local_persona._validate_persona_prose(prose, **_blocks_kwargs())
    assert ok is False


def test_validate_rejects_invented_number_only() -> None:
    pat_blocks = {
        "brief_block": "<brief>Pat signer</brief>",
        "documents_block": "<documents>Pat filed on Monday.</documents>",
        "architect_brief_block": "<architect_brief>lead_pin_id P1</architect_brief>",
    }
    prose = "Pat filed on Monday 99 times."
    _, ok = local_persona._validate_persona_prose(prose, **pat_blocks)
    assert ok is False


@pytest.mark.parametrize(
    "prose",
    [
        "Pat met Zaphod Beetlebrox downtown.",
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
    _, ok = local_persona._validate_persona_prose(prose, **pat_blocks)
    assert ok is False


def test_persona_author_fail_closed_skips_branch(monkeypatch: pytest.MonkeyPatch) -> None:
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
    payload = asyncio.run(
        local_persona.WritingLocalPersonaAuthorHandler().execute(None, _correspondence_ctx())
    ).json
    assert payload.get("ok") is False
    assert payload.get("refused") == "persona_validation_failed"
    assert payload.get("persona_refused") == "persona_validation_failed"
    assert payload.get("persona_rejected_text")
    assert "persona_prose" not in payload
    assert "_skipped" not in payload
    assert published and published[0].signal == "writing.persona.refused"
    assert published[0].role == "observation"


def test_persona_author_model_exception_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
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
        local_persona.WritingLocalPersonaAuthorHandler().execute(None, _correspondence_ctx())
    ).json
    assert payload.get("ok") is False
    assert payload.get("persona_refused") == "persona_author_failed"


def test_persona_author_missing_prompt_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_persona, "load_prompts", lambda: {})
    payload = asyncio.run(
        local_persona.WritingLocalPersonaAuthorHandler().execute(None, _correspondence_ctx())
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
    payload = asyncio.run(
        local_persona.WritingLocalPersonaAuthorHandler().execute(None, _correspondence_ctx())
    ).json
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
    event = judge_events.WritingPersonaRefused(reason="persona_validation_failed")
    assert event.signal == "writing.persona.refused"
    assert event.role == "observation"
    assert event.payload["reason"] == "persona_validation_failed"
