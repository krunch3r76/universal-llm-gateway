"""Offline checks for local persona author and writer_local prompts."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
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

pytestmark = pytest.mark.offline

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
                    "pin_ledger": [
                        {
                            "excerpt": (
                                "Kaywan Joseph Mansubi Assessment Appeals 25.7489 "
                                "Los Gatos October 27 2026"
                            )
                        }
                    ],
                    "brief_block": "<brief>channel: email</brief>",
                    "documents_block": "<documents><pin id='P1'>Hearing Tuesday</pin></documents>",
                    "verbatim_notes_block": "",
                    "source_message_block": "",
                }
            ),
            "source_obligations": _Out(
                {"obligations": [], "source_obligations_block": ""}
            ),
            "prompt_architect": _Out(
                {
                    "architect_brief_block": "<architect_brief>lead_pin_id: P1</architect_brief>"
                }
            ),
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


def test_validate_persona_rejects_invented_token() -> None:
    intake = {"recipient": "Pat", "channel": "email"}
    pin_ledger: list[dict[str, Any]] = [{"excerpt": "Pat applied on Monday."}]
    prose = "I am Zaphod Beetlebrox and Pat applied on Monday."
    _, ok = local_persona._validate_persona_prose(prose, pin_ledger, intake, [])
    assert ok is False


def test_persona_author_fail_closed_skips_branch(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "_call_model",
        _bad_model,
    )
    payload = asyncio.run(
        local_persona.WritingLocalPersonaAuthorHandler().execute(None, _correspondence_ctx())
    ).json
    assert payload.get("ok") is False
    assert payload.get("refused") == "persona_validation_failed"
    assert payload.get("_skipped") is True


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
                    "I am Kaywan Joseph Mansubi writing to AABHearing about "
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


def test_writer_yaml_unchanged_vs_master() -> None:
    import subprocess

    lane = _V1 / "prompts" / "writer.yaml"
    master_bytes = subprocess.check_output(
        [
            "git",
            "show",
            "master:pipelines/writing/v1/prompts/writer.yaml",
        ],
        cwd=_V1.parents[2],
    )
    assert lane.read_bytes() == master_bytes
