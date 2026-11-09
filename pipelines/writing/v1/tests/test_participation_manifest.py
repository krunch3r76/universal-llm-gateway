"""Participation manifest rows for writer-specialist finalize."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from systems.pipeline.core.conditions import ConditionEvaluator
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

participation = importlib.import_module("writing_v1_handlers.participation")
independence = importlib.import_module("writing_v1_handlers.independence")
finalize_payload = importlib.import_module("writing_v1_handlers.finalize_payload")

pytestmark = pytest.mark.offline


class _Out:
    def __init__(
        self,
        payload: dict[str, Any] | None,
        *,
        raw: str = "",
        error: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int | None = 0,
        model_id: str | None = None,
    ) -> None:
        self.json = payload
        self.raw = raw
        self.error = error
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.model_id = model_id


def _sworn_assemble() -> dict[str, Any]:
    return {
        "refused": None,
        "channel": "email",
        "check_class": "sworn",
    }


def test_all_six_executed() -> None:
    text = "Draft body"
    outputs = {
        "assemble": _Out(_sworn_assemble()),
        "local_persona_author": _Out({"ok": True, "persona_prose": "I write."}),
    }
    models = independence.load_writing_models()
    for step_name, alias in (
        ("drafter_openai", "drafter_openai"),
        ("drafter_google", "drafter_google"),
        ("drafter_xai", "drafter_xai"),
        ("drafter_local_hermes", "drafter_local_hermes"),
        ("drafter_local_ernie", "drafter_local_ernie"),
        ("drafter_anthropic", "drafter_anthropic"),
    ):
        outputs[step_name] = _Out(
            None,
            raw=text,
            prompt_tokens=10,
            completion_tokens=5,
            model_id=models.get(alias),
        )
    rows = participation.build_participation_manifest(
        outputs,
        options={"output": "envelope", "full_drafter_fanout": True},
        models=models,
    )
    assert len(rows) == 6
    assert all(row["state"] == "executed" for row in rows)
    assert all(row["candidate_sha256"] for row in rows)
    summary = participation.build_participation_summary(rows)
    assert summary.startswith("6/6 executed")


def test_unmetered_completion_tokens_none() -> None:
    outputs = {
        "assemble": _Out(_sworn_assemble()),
        "local_persona_author": _Out({"ok": True, "persona_prose": "I write."}),
        "drafter_openai": _Out(
            None,
            raw="Body",
            prompt_tokens=1,
            completion_tokens=None,
            model_id="openai/gpt-5.4",
        ),
    }
    models = independence.load_writing_models()
    rows = participation.build_participation_manifest(
        outputs,
        options={"output": "envelope", "full_drafter_fanout": True},
        models=models,
    )
    openai = next(r for r in rows if r["step"] == "drafter_openai")
    assert openai["state"] == "executed_unmetered"
    assert openai["completion_tokens"] is None


def test_unmetered_state() -> None:
    outputs = {
        "assemble": _Out(_sworn_assemble()),
        "local_persona_author": _Out({"ok": True, "persona_prose": "I write."}),
        "drafter_openai": _Out(
            None,
            raw="Body",
            prompt_tokens=1,
            completion_tokens=0,
            model_id="openai/gpt-5.4",
        ),
    }
    models = independence.load_writing_models()
    rows = participation.build_participation_manifest(
        outputs,
        options={"output": "envelope", "full_drafter_fanout": True},
        models=models,
    )
    openai = next(r for r in rows if r["step"] == "drafter_openai")
    assert openai["state"] == "executed_unmetered"
    summary = participation.build_participation_summary(rows)
    assert "unmetered:drafter_openai" in summary


def test_policy_skip_labeled() -> None:
    outputs = {"assemble": _Out({"refused": None, "channel": "email", "check_class": "correspondence"})}
    rows = participation.build_participation_manifest(
        outputs,
        options={"output": "envelope", "full_drafter_fanout": False},
    )
    hermes = next(r for r in rows if r["step"] == "drafter_local_hermes")
    assert hermes["state"] == "skipped_policy"
    anthropic = next(r for r in rows if r["step"] == "drafter_anthropic")
    assert anthropic["state"] != "skipped_policy"


def test_persona_skip_dependency() -> None:
    outputs = {
        "assemble": _Out(_sworn_assemble()),
        "local_persona_author": _Out(
            {"ok": False, "persona_refused": "persona_validation_failed"}
        ),
    }
    rows = participation.build_participation_manifest(
        outputs,
        options={"output": "envelope", "full_drafter_fanout": True},
    )
    hermes = next(r for r in rows if r["step"] == "drafter_local_hermes")
    assert hermes["state"] == "skipped_dependency"
    assert hermes["dependency"] == "local_persona_author:persona_validation_failed"


def test_partial_provider_failure() -> None:
    outputs = {
        "assemble": _Out(_sworn_assemble()),
        "local_persona_author": _Out({"ok": True, "persona_prose": "voice"}),
        "drafter_openai": _Out(
            None,
            raw="ok",
            completion_tokens=3,
            model_id="openai/gpt-5.4",
        ),
        "drafter_google": _Out(None, raw="", error="timeout"),
    }
    rows = participation.build_participation_manifest(
        outputs,
        options={"output": "envelope", "full_drafter_fanout": True},
    )
    google = next(r for r in rows if r["step"] == "drafter_google")
    assert google["state"] == "failed"


def test_manifest_order_matches_yaml() -> None:
    rows = participation.build_participation_manifest({}, options={})
    names = [row["step"] for row in rows]
    assert names == [
        "drafter_openai",
        "drafter_google",
        "drafter_xai",
        "drafter_local_hermes",
        "drafter_local_ernie",
        "drafter_anthropic",
    ]


def test_manifest_order_ignores_shuffled_outputs() -> None:
    shuffled = {
        "drafter_anthropic": _Out(None, raw="z", completion_tokens=1, model_id="m"),
        "drafter_openai": _Out(None, raw="a", completion_tokens=1, model_id="m"),
        "drafter_local_ernie": _Out(None, raw="e", completion_tokens=1, model_id="m"),
        "drafter_xai": _Out(None, raw="x", completion_tokens=1, model_id="m"),
        "drafter_google": _Out(None, raw="g", completion_tokens=1, model_id="m"),
        "drafter_local_hermes": _Out(None, raw="h", completion_tokens=1, model_id="m"),
        "assemble": _Out(_sworn_assemble()),
        "local_persona_author": _Out({"ok": True, "persona_prose": "voice"}),
    }
    rows = participation.build_participation_manifest(
        shuffled,
        options={"output": "envelope", "full_drafter_fanout": True},
    )
    assert [r["step"] for r in rows] == [
        "drafter_openai",
        "drafter_google",
        "drafter_xai",
        "drafter_local_hermes",
        "drafter_local_ernie",
        "drafter_anthropic",
    ]


def test_requested_models_from_writing_models_yaml() -> None:
    models = independence.load_writing_models()
    rows = participation.build_participation_manifest({}, options={}, models=models)
    for row in rows:
        alias = row["alias"]
        assert row["requested_model"] == models.get(alias)


def test_conditions_match_participation_states() -> None:
    from systems.pipeline.core.handlers.step_output import StepOutput

    raw = yaml.safe_load(
        (_V1 / "writer-specialist-v1.yaml").read_text(encoding="utf-8")
    )
    by_name = {step.name: step for step in PipelineSpec(**raw).steps}
    evaluator = ConditionEvaluator()

    def step(data: dict[str, Any]) -> StepOutput:
        return StepOutput(raw="", json=data)

    eval_ctx = {
        "assemble": step(_sworn_assemble()),
        "local_persona_author": step({"ok": True, "persona_prose": "x"}),
    }
    opts = {"output": "envelope", "full_drafter_fanout": True}
    manifest_outputs: dict[str, Any] = {
        "assemble": _Out(_sworn_assemble()),
        "local_persona_author": _Out({"ok": True, "persona_prose": "x"}),
    }
    for step_name in (
        "drafter_openai",
        "drafter_google",
        "drafter_xai",
        "drafter_local_hermes",
        "drafter_local_ernie",
        "drafter_anthropic",
    ):
        cond = by_name[step_name].condition or ""
        active = evaluator.evaluate(cond, eval_ctx, opts)
        manifest_outputs[step_name] = _Out(
            None, raw="text", completion_tokens=2, model_id="m"
        )
        rows = participation.build_participation_manifest(
            manifest_outputs,
            options=opts,
        )
        row = next(r for r in rows if r["step"] == step_name)
        if active:
            assert row["state"] in ("executed", "executed_unmetered", "failed")
        else:
            assert row["state"].startswith("skipped_")


def test_load_writing_models_uses_parent_not_v1_models_yaml() -> None:
    v1_models_path = _V1 / "models.yaml"
    original = v1_models_path.read_text(encoding="utf-8")
    sentinel = "openai/LEG1A-SENTINEL-ONLY-IN-V1"
    divergent = original.replace("openai/gpt-5.4", sentinel, 1)
    try:
        v1_models_path.write_text(divergent, encoding="utf-8")
        table = independence.load_writing_models()
        assert table["drafter_openai"] == "openai/gpt-5.4"
        assert table["reviser"] == "qwen3-5-9b-q8-0-131072"
    finally:
        v1_models_path.write_text(original, encoding="utf-8")


def test_missing_condition_row_skipped_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conds = participation._load_conditions().copy()
    conds["drafter_google"] = ""
    monkeypatch.setattr(participation, "_CONDITIONS", conds)
    outputs = {
        "assemble": _Out(_sworn_assemble()),
        "local_persona_author": _Out({"ok": True, "persona_prose": "voice"}),
        "drafter_google": _Out(None, raw="text", completion_tokens=2, model_id="m"),
    }
    rows = participation.build_participation_manifest(
        outputs,
        options={"output": "envelope", "full_drafter_fanout": True},
    )
    google = next(r for r in rows if r["step"] == "drafter_google")
    assert google["state"] == "skipped_unconfigured"


def _minimal_finalize_kwargs(
    outputs_for_participation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "coverage_map": [],
        "draft": {},
        "initial": [],
        "final": None,
        "dropped_facts": [],
        "added_facts": [],
        "drift_check": {},
        "review": None,
        "held_review": False,
        "review_ok": False,
        "review_reason": None,
        "assemble": {},
        "live_independence": None,
        "revision": None,
        "passed": False,
        "unresolved": [],
        "selected": "",
        "rhythm_reverted": False,
        "outputs": {},
        "revision_discarded": None,
        "models": independence.load_writing_models(),
        "revision_ran": False,
        "reviewer_live": "",
        "usage": {},
        "est_cost": None,
        "cost_note": None,
        "filing_text": None,
        "selected_body": "",
        "obl_refusal": None,
        "outputs_for_participation": outputs_for_participation,
        "options": {"output": "envelope", "full_drafter_fanout": True},
    }


def test_finalize_manifest_exception_sets_participation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        raise ValueError("manifest_boom")

    monkeypatch.setattr(finalize_payload, "build_participation_manifest", _boom)
    outputs = {
        "assemble": _Out(_sworn_assemble()),
        "local_persona_author": _Out({"ok": True, "persona_prose": "voice"}),
    }
    payload = finalize_payload.build_finalize_payload(
        **_minimal_finalize_kwargs(outputs)
    )
    assert payload.get("participation") is None
    assert payload.get("participation_summary") is None
    assert payload.get("participation_error") == "ValueError"
