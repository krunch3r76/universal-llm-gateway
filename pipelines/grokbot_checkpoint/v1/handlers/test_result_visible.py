"""Pipeline result step exposes JSON in raw/text for MCP pipeline(op=result)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from systems.pipeline.core.handlers.protocol import StepOutput

from .result import GrokbotCheckpointResultHandler, assemble

pytestmark = pytest.mark.offline


def _wrap(outputs: dict) -> dict[str, StepOutput]:
    return {name: StepOutput(raw="", json=payload) for name, payload in outputs.items()}


async def _run_result(options: dict, outputs: dict) -> StepOutput:
    handler = GrokbotCheckpointResultHandler()
    ctx = SimpleNamespace(options=options, outputs=_wrap(outputs))
    return await handler.execute(step=None, context=ctx)


@pytest.mark.asyncio
async def test_source_refusal_visible_in_result_raw() -> None:
    options = {"house": "hm1", "n": 1, "mode": "seal"}
    outputs = {
        "admit": {"refused": None},
        "source": {
            "refused": {
                "code": "source.slot_mismatch",
                "message": "transcribed slot refuses raw pour",
            },
        },
    }
    out = await _run_result(options, outputs)
    parsed = json.loads(out.raw)
    assert parsed["refused"]["code"] == "source.slot_mismatch"


@pytest.mark.asyncio
async def test_verify_resume_checks_in_result_raw() -> None:
    options = {"house": "hm1", "n": 1, "mode": "verify_resume"}
    verify_payload = {
        "refused": None,
        "checks": {"authority": "seal", "authority_sha": True},
        "warnings": [],
        "writes": 0,
        "posts": 0,
    }
    outputs = {
        "admit": {"refused": None},
        "source": {"refused": None, "messages_sha256": "a" * 64, "provenance": "raw"},
        "envelope": {"snapshot_uri": "notes/snap.json", "sha256": "b" * 64},
        "seal": {"session_id": "sess-1", "refused": None},
        "card": {"uri": "notes/card.md", "written_sha256": "c" * 64},
        "post": {"turn": 2},
        "fact": {"turn": 3},
        "await_verify": {"state": "in_sync", "turn": 4},
        "verify_resume": verify_payload,
    }
    out = await _run_result(options, outputs)
    parsed = json.loads(out.raw)
    assert parsed["verify_resume"]["checks"]["authority"] == "seal"


@pytest.mark.asyncio
async def test_result_text_equals_canonical_json() -> None:
    options = {"house": "hm1", "n": 1, "mode": "seal"}
    outputs = {
        "admit": {"refused": None},
        "source": {
            "refused": {"code": "pour.window", "message": "0-based window"},
        },
    }
    payload = assemble(options, _wrap(outputs))
    expected = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    out = await _run_result(options, outputs)
    assert out.text == expected
    assert out.raw == expected
