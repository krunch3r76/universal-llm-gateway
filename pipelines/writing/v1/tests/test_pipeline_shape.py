"""Shape checks for writer-specialist v1 YAML, prompts, rates, and registration."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml
from systems.pipeline.core.pipeline_config import PipelineSpec

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

handlers = sys.modules[_PKG]

pytestmark = pytest.mark.offline

_STEP_ORDER = [
    "assemble",
    "prompt_architect",
    "drafter_openai",
    "drafter_google",
    "drafter_xai",
    "drafter_local_hermes",
    "drafter_local_ernie",
    "drafter_anthropic",
    "draft",
    "independence",
    "fact_ceiling_audit",
    "provenance_check",
    "prose_rhythm_pass",
    "fact_ceiling_final",
    "finalize",
]
_GO = (
    "assemble.json.get('refused') is None and "
    "options.get('output', 'envelope') != 'packet'"
)
_GO2 = _GO + " and draft.json.get('refused') is None"
_IND = "independence.json.get('refused') is None"
_AUDIT = _GO2 + " and " + _IND
_QWEN_SERVED = "qwen3-5-9b-q8-0-131072"
_ERNIE = "ernie-4-5-21b-a3b-pt-q8-0-65536"
_ANTHROPIC = "anthropic/claude-sonnet-5-5"
_CLOUD_DRAFTERS = (
    "drafter_openai",
    "drafter_google",
    "drafter_xai",
    "drafter_anthropic",
)


class _Router:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, type]] = []

    def register_domain_handler_class(
        self, domain: str, step_type: str, cls: type
    ) -> None:
        self.rows.append((domain, step_type, cls))


def test_pipeline_shape() -> None:
    raw = yaml.safe_load(
        (_V1 / "writer-specialist-v1.yaml").read_text(encoding="utf-8")
    )
    spec = PipelineSpec(**raw)
    assert spec.id == "writer-specialist-v1"
    assert spec.type == "writing"
    assert [step.name for step in spec.steps] == _STEP_ORDER
    by_name = {step.name: step for step in spec.steps}
    assert by_name["draft"].type == "writing_draft_selector_v1"
    assert by_name["prompt_architect"].type == "writing_prompt_architect_v1"
    for name in _CLOUD_DRAFTERS:
        step = by_name[name]
        assert step.type == "generate"
        params = getattr(step, "generation_parameters", None) or {}
        assert params.get("max_tokens") == 16384
        assert "response_format" not in params
    for name in ("drafter_local_hermes", "drafter_local_ernie"):
        params = getattr(by_name[name], "generation_parameters", None) or {}
        assert params.get("max_tokens") == 3072
    assert by_name["drafter_anthropic"].model_ref == "drafter_anthropic"
    banned = ("cdp", "web-anthropic", "cursor_pool")
    blob = yaml.safe_dump(raw).lower()
    for token in banned:
        assert token not in blob
    writing_models = yaml.safe_load(
        (_V1.parent / "models.yaml").read_text(encoding="utf-8")
    )["models"]
    assert writing_models["drafter_anthropic"]["model"] == _ANTHROPIC
    assert writing_models["verifier"]["model"] == _QWEN_SERVED
    assert writing_models["writer"]["model"] == _QWEN_SERVED
    fc_final = by_name["fact_ceiling_final"]
    assert any(
        entry.step == "prose_rhythm_pass" and "raw" in entry.fields
        for entry in (fc_final.reads_from or [])
    )
    assert writing_models["drafter_openai"]["model"] == "openai/gpt-5.4"
    independence_mod = importlib.import_module("writing_v1_handlers.independence")
    assert independence_mod.model_family("google/gemini-2.5-pro") == "google"
    assert independence_mod.model_family(_ERNIE) == "ernie"
    router = _Router()
    handlers.register_handlers(router)
    registered = {step_type for _domain, step_type, _cls in router.rows}
    assert "writing_prompt_architect_v1" in registered
    assert "writing_draft_selector_v1" in registered
    assert "writing_seat_dispatch_v1" in registered


def test_go_conditions_on_step_outputs() -> None:
    from systems.pipeline.core.conditions import ConditionEvaluator
    from systems.pipeline.core.handlers.step_output import StepOutput

    raw = yaml.safe_load(
        (_V1 / "writer-specialist-v1.yaml").read_text(encoding="utf-8")
    )
    by_name = {step.name: step for step in PipelineSpec(**raw).steps}
    evaluator = ConditionEvaluator()

    def step(data: dict) -> StepOutput:
        return StepOutput(raw="", json=data)

    clean = {
        "assemble": step({"refused": None}),
        "draft": step({"refused": None, "draft": "text"}),
        "independence": step({"refused": None}),
        "fact_ceiling_audit": step({"verdict": "ship", "findings": []}),
    }
    assert evaluator.evaluate(by_name["fact_ceiling_audit"].condition, clean, {}) is True
    assert evaluator.evaluate(by_name["prose_rhythm_pass"].condition, clean, {}) is True

    refused_draft = {
        "assemble": step({"refused": None}),
        "draft": step({"refused": "no_draft_candidates"}),
    }
    assert evaluator.evaluate(by_name["independence"].condition, refused_draft, {}) is False
