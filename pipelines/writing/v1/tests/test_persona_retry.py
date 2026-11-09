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
participation = importlib.import_module("writing_v1_handlers.participation")
independence = importlib.import_module("writing_v1_handlers.independence")

pytestmark = pytest.mark.offline

_FIXTURE = json.loads(
    (_V1 / "tests" / "fixtures" / "monterey_monday_persona.json").read_text(
        encoding="utf-8"
    )
)


class _StepOut:
    def __init__(self, payload: dict[str, Any] | None, **_: Any) -> None:
        self.json = payload


class _Ctx:
    def __init__(
        self, outputs: dict[str, _StepOut], *, options: dict[str, Any] | None = None
    ) -> None:
        self.outputs = outputs
        self.options = options or {}


def _monterey_ctx(*, persona_retry: bool) -> _Ctx:
    data = dict(_FIXTURE)
    return _Ctx(
        {
            "assemble": _StepOut(
                {
                    "refused": None,
                    "intake": dict(data["intake"]),
                    "brief_block": data["brief_block"],
                    "documents_block": data["documents_block"],
                }
            ),
            "prompt_architect": _StepOut(
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
    assert payload["persona_attempts"][1].get("rejected_text") is None
    assert published == []


def test_retry_exhaustion_skips_locals(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _FIXTURE["judged_invented_token"]
    prompts_seen: list[str] = []

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
    payload = asyncio.run(handler.execute(None, _monterey_ctx(persona_retry=True))).json
    assert payload.get("ok") is False
    assert payload.get("persona_refused") == "persona_validation_failed"
    assert len(prompts_seen) == 2
    assert token in prompts_seen[1]
    assert f"Do not introduce: {token}" in prompts_seen[1]
    assert len(payload.get("persona_attempts") or []) == 2
    assert len(published) == 1
    event = published[0]
    assert event.payload["reason"] == "persona_validation_failed"
    assert event.payload.get("judged_invented") == [token]
    manifest_out = {
        "assemble": _StepOut(
            {
                "refused": None,
                "intake": dict(_FIXTURE["intake"]),
                "brief_block": _FIXTURE["brief_block"],
                "documents_block": _FIXTURE["documents_block"],
                "check_class": "sworn",
                "channel": "email",
            }
        ),
        "local_persona_author": _StepOut(payload),
    }
    rows = participation.build_participation_manifest(
        manifest_out,
        options={"output": "envelope", "full_drafter_fanout": True},
    )
    hermes = next(r for r in rows if r["step"] == "drafter_local_hermes")
    ernie = next(r for r in rows if r["step"] == "drafter_local_ernie")
    assert hermes["state"] == "skipped_dependency"
    assert ernie["state"] == "skipped_dependency"
    assert hermes["completion_tokens"] in (0, None)
    assert ernie["completion_tokens"] in (0, None)
    summary = participation.build_participation_summary(rows)
    assert "drafter_local_hermes" in summary
    assert "drafter_local_ernie" in summary


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
    assert "invented_or_invalid_prose" not in text


def test_build_retry_feedback_names_deterministic_detail() -> None:
    verdict = local_persona.PersonaValidationResult(
        text="- bullet",
        ok=False,
        refused="persona_validation_failed",
        detail="list_structure",
    )
    text = persona_retry.build_retry_feedback(verdict)
    assert "list_structure" in text
    assert "invented_or_invalid_prose" not in text


def test_persona_retry_string_false_not_enabled() -> None:
    assert persona_retry.persona_retry_enabled({"persona_retry": "false"}) is False
    assert persona_retry.persona_retry_enabled({"persona_retry": True}) is True


def test_deterministic_list_failure_retries_then_refuses() -> None:
    calls = {"judge": 0, "author": 0}
    list_prose = (
        "You write as Pat Rivera about the Monterey restaurant.\n"
        "- open Tuesday through Sunday"
    )
    good = _FIXTURE["attempt2_prose"]

    async def _caller(prompt: str) -> str:
        calls["author"] += 1
        if "list_structure" in prompt:
            return good
        return list_prose

    async def _judge(
        _fenced: str, candidates: list[local_persona.Candidate]
    ) -> str:
        calls["judge"] += 1
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

    handler = local_persona.WritingLocalPersonaAuthorHandler(
        caller=_caller, judge_caller=_judge
    )
    payload = asyncio.run(
        handler.execute(None, _monterey_ctx(persona_retry=True))
    ).json
    assert calls["author"] == 2
    assert calls["judge"] == 1
    assert payload.get("ok") is True


def test_judge_invalid_no_retry() -> None:
    calls = {"author": 0, "judge": 0}

    async def _caller(prompt: str) -> str:
        calls["author"] += 1
        return _FIXTURE["attempt1_prose"]

    async def _judge(
        _fenced: str, _candidates: list[local_persona.Candidate]
    ) -> str:
        calls["judge"] += 1
        return "not-json"

    handler = local_persona.WritingLocalPersonaAuthorHandler(
        caller=_caller, judge_caller=_judge
    )
    payload = asyncio.run(
        handler.execute(None, _monterey_ctx(persona_retry=True))
    ).json
    assert payload.get("persona_refused") == "persona_judge_invalid"
    assert calls["author"] == 1
    assert calls["judge"] == 1
    assert len(payload.get("persona_attempts") or []) == 1


def test_prompt_missing_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(persona_retry, "render_persona_prompt", lambda **_k: "")

    async def _caller(prompt: str) -> str:
        return _FIXTURE["attempt1_prose"]

    handler = local_persona.WritingLocalPersonaAuthorHandler(
        caller=_caller, judge_caller=_judge_accepts_all()
    )
    payload = asyncio.run(
        handler.execute(None, _monterey_ctx(persona_retry=True))
    ).json
    assert payload.get("persona_refused") == "persona_prompt_missing"
    assert len(payload.get("persona_attempts") or []) == 1


def test_list_structure_exhaustion_no_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    list_prose = "You write plainly.\n1. First item"

    async def _caller(_prompt: str) -> str:
        return list_prose

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
        judge_caller=_judge_accepts_all(),
    )
    payload = asyncio.run(
        handler.execute(None, _monterey_ctx(persona_retry=True))
    ).json
    assert payload.get("persona_refused") == "persona_validation_failed"
    assert payload.get("detail") == "list_structure"
    assert len(payload.get("persona_attempts") or []) == 2
    assert payload["persona_attempts"][0]["detail"] == "list_structure"


def test_judge_call_counts_and_attempt_tokens() -> None:
    judge_calls: list[int] = []
    token = _FIXTURE["judged_invented_token"]

    class _AuthorResult:
        def __init__(self, content: str, *, attempt: int) -> None:
            self.content = content
            self.prompt_tokens = 11 * attempt
            self.completion_tokens = 7 * attempt

    async def _caller(prompt: str) -> _AuthorResult:
        attempt = 1 if "Your prior attempt" not in prompt else 2
        body = _FIXTURE["attempt1_prose"] if attempt == 1 else _FIXTURE["attempt2_prose"]
        return _AuthorResult(body, attempt=attempt)

    async def _judge(
        _fenced: str, candidates: list[local_persona.Candidate]
    ) -> str:
        judge_calls.append(len(candidates))
        rows = []
        for cand in candidates:
            kind = (
                "new_entity_or_fact"
                if cand.token == token and len(judge_calls) == 1
                else "ordinary_language"
            )
            rows.append(
                {
                    "token": cand.token,
                    "basis": "",
                    "reading": "ok",
                    "kind": kind,
                }
            )
        return json.dumps({"judgments": rows})

    handler = local_persona.WritingLocalPersonaAuthorHandler(
        caller=_caller, judge_caller=_judge
    )
    payload = asyncio.run(
        handler.execute(None, _monterey_ctx(persona_retry=True))
    ).json
    assert payload.get("ok") is True
    assert len(judge_calls) == 2
    attempts = payload.get("persona_attempts") or []
    assert attempts[0]["prompt_tokens"] == 11
    assert attempts[0]["completion_tokens"] == 7
    assert attempts[1]["prompt_tokens"] == 22
    assert attempts[1]["completion_tokens"] == 14


def test_validate_exception_preserves_persona_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_a: Any, **_k: Any) -> local_persona.PersonaValidationResult:
        raise RuntimeError("validate_exploded")

    monkeypatch.setattr(local_persona, "validate_persona_prose", _boom)

    async def _caller(_prompt: str) -> str:
        return _FIXTURE["attempt1_prose"]

    handler = local_persona.WritingLocalPersonaAuthorHandler(
        caller=_caller, judge_caller=_judge_accepts_all()
    )
    payload = asyncio.run(
        handler.execute(None, _monterey_ctx(persona_retry=True))
    ).json
    assert payload.get("persona_refused") == "persona_author_failed"
    assert len(payload.get("persona_attempts") or []) == 1
    assert payload["persona_attempts"][0].get("error") == "RuntimeError"
