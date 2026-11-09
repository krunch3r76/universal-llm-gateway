"""Persona retry policy and participation-adjacent author loop tests."""

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
persona_retry = importlib.import_module("writing_v1_handlers.persona_retry")

pytestmark = pytest.mark.offline

_FIXTURE = json.loads(
    (_V1 / "tests" / "fixtures" / "monterey_monday_persona.json").read_text(
        encoding="utf-8"
    )
)


class _Out:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.json = payload


class _Ctx:
    def __init__(
        self, outputs: dict[str, _Out], *, options: dict[str, Any] | None = None
    ) -> None:
        self.outputs = outputs
        self.options = options or {}


def _monterey_ctx(*, persona_retry: bool) -> _Ctx:
    data = dict(_FIXTURE)
    return _Ctx(
        {
            "assemble": _Out(
                {
                    "refused": None,
                    "intake": dict(data["intake"]),
                    "brief_block": data["brief_block"],
                    "documents_block": data["documents_block"],
                }
            ),
            "prompt_architect": _Out(
                {"architect_brief_block": data["architect_brief_block"]}
            ),
        },
        options={"persona_retry": persona_retry},
    )


def _judge_invented(token: str) -> Any:
    async def _caller(_fenced: str, candidates: list[local_persona.Candidate]) -> str:
        rows = []
        for cand in candidates:
            kind = (
                "new_entity_or_fact"
                if cand.token == token
                else "ordinary_language"
            )
            rows.append(
                {
                    "token": cand.token,
                    "basis": "",
                    "reading": "Unit-test reading.",
                    "kind": kind,
                }
            )
        return json.dumps({"judgments": rows})

    return _caller


def _judge_accepts_all() -> Any:
    async def _caller(_fenced: str, candidates: list[local_persona.Candidate]) -> str:
        rows = [
            {
                "token": cand.token,
                "basis": "",
                "reading": "ok",
                "kind": "ordinary_language",
            }
            for cand in candidates
        ]
        return json.dumps({"judgments": rows})

    return _caller


def test_retry_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts_seen: list[str] = []
    token = _FIXTURE["judged_invented_token"]

    async def _caller(prompt: str) -> str:
        prompts_seen.append(prompt)
        return _FIXTURE["attempt1_prose"]

    published: list[Any] = []

    def _capture(_self: object, _ctx: object, event: Any) -> None:
        published.append(event)

    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_publish_bus_event",
        _capture,
    )
    handler = local_persona.WritingLocalPersonaAuthorHandler(
        caller=_caller,
        judge_caller=_judge_invented(token),
    )
    payload = asyncio.run(handler.execute(None, _monterey_ctx(persona_retry=False))).json
    assert payload.get("ok") is False
    assert len(prompts_seen) == 1
    assert "Your prior attempt introduced" not in prompts_seen[0]
    assert len(payload.get("persona_attempts") or []) == 1
    assert len(published) == 1


def test_second_attempt_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts_seen: list[str] = []
    token = _FIXTURE["judged_invented_token"]

    async def _caller(prompt: str) -> str:
        prompts_seen.append(prompt)
        if "Your prior attempt introduced" in prompt:
            return _FIXTURE["attempt2_prose"]
        return _FIXTURE["attempt1_prose"]

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
        return type("Result", (), {"content": _FIXTURE["attempt2_prose"]})()

    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_call_model",
        _good_model,
    )
    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_publish_bus_event",
        _capture,
    )
    handler = local_persona.WritingLocalPersonaAuthorHandler(
        caller=_caller,
        judge_caller=_judge_invented(token),
    )
    payload = asyncio.run(handler.execute(None, _monterey_ctx(persona_retry=True))).json
    assert payload.get("ok") is True
    assert len(prompts_seen) == 2
    assert token in prompts_seen[1]
    assert len(payload.get("persona_attempts") or []) == 2
    assert payload["persona_attempts"][0]["ok"] is False
    assert payload["persona_attempts"][1]["ok"] is True
    assert published == []


def test_retry_exhaustion_skips_locals(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _FIXTURE["judged_invented_token"]

    async def _caller(prompt: str) -> str:
        return _FIXTURE["attempt1_prose"]

    published: list[Any] = []

    def _capture(_self: object, _ctx: object, event: Any) -> None:
        published.append(event)

    monkeypatch.setattr(
        local_persona.WritingLocalPersonaAuthorHandler,
        "_publish_bus_event",
        _capture,
    )
    handler = local_persona.WritingLocalPersonaAuthorHandler(
        caller=_caller,
        judge_caller=_judge_invented(token),
    )
    payload = asyncio.run(handler.execute(None, _monterey_ctx(persona_retry=True))).json
    assert payload.get("ok") is False
    assert len(payload.get("persona_attempts") or []) == 2
    assert len(published) == 1


def test_judge_down_no_retry() -> None:
    calls = {"n": 0}

    async def _caller(prompt: str) -> str:
        calls["n"] += 1
        return _FIXTURE["attempt1_prose"]

    async def _judge_raises(
        _fenced: str, _candidates: list[local_persona.Candidate]
    ) -> str:
        raise RuntimeError("judge_down")

    handler = local_persona.WritingLocalPersonaAuthorHandler(
        caller=_caller,
        judge_caller=_judge_raises,
    )
    payload = asyncio.run(
        handler.execute(
            None,
            _monterey_ctx(persona_retry=True),
        )
    ).json
    assert payload.get("persona_refused") == "persona_judge_unavailable"
    assert calls["n"] == 1
    assert len(payload.get("persona_attempts") or []) == 1


def test_author_failed_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
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
            None, _monterey_ctx(persona_retry=True)
        )
    ).json
    assert payload.get("persona_refused") == "persona_author_failed"
    assert payload.get("error") == "RuntimeError"
    assert len(payload.get("persona_attempts") or []) == 1


def test_build_retry_feedback_lists_invented() -> None:
    verdict = local_persona.PersonaValidationResult(
        text="x",
        ok=False,
        refused="persona_validation_failed",
        judged_invented=["Monday"],
    )
    text = persona_retry.build_retry_feedback(verdict)
    assert "Monday" in text
    assert "Do not introduce" in text
