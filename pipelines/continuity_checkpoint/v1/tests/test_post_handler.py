"""Preamble sidecar + post handler integration (uncapped orientation)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_V1 = Path(__file__).resolve().parents[1]
if str(_V1) not in sys.path:
    sys.path.insert(0, str(_V1))

from handlers._card_patch import (  # noqa: E402
    RESIDUE_CAP_CHARS,
    normalize_preamble,
    render_preamble_markdown,
    validate_worker_payload,
)
from handlers.post import ContinuityCheckpointPostHandler  # noqa: E402

pytestmark = pytest.mark.offline


def _long_preamble(chars: int) -> dict[str, str]:
    pad = "x" * max(chars, 0)
    return {
        "stance": f"Stance line {pad}",
        "facts": "One observed fact.",
        "next_action": "Do the bounded next move; stop when verified.",
    }


def test_validate_worker_payload_accepts_8000_char_preamble() -> None:
    preamble = _long_preamble(8000)
    payload = {
        "card_patch": {"resume_open": "In one line: test"},
        "mission": "resume",
        **preamble,
    }
    ok, reason = validate_worker_payload(payload)
    assert ok
    assert reason == "ok"


class _Ctx:
    execution_id = "exec-preamble"
    dispatch_thread_id = "16416"
    options = {"thread": "16416", "surface": "cursor", "from_agent": "continuity"}
    outputs = {
        "seal": {
            "json": {
                "transcript_id": "uuid-1",
                "turn_count": 3,
                "session_id": "cursor-test",
                "messages_sha256": "abc",
                "refused": None,
            }
        },
        "resolve": {"json": {}},
        "tail_mechanical": {"json": {}},
        "score": {"json": {}},
    }


class _Step:
    pass


@pytest.mark.asyncio
async def test_post_writes_full_sidecar_and_short_residue() -> None:
    preamble = _long_preamble(900)
    pre = {
        "residue": "Mission: ignored for pointer when preamble present",
        "residue_source": "model",
        "mission": "resume house",
        "executor": "cursor-sdk",
        "preamble": preamble,
        **preamble,
    }
    ctx = _Ctx()
    ctx.outputs["pre_consolidate"] = {"json": pre}
    send = AsyncMock(return_value=({"turn_number": 11}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        out = await handler.execute(_Step(), ctx)
    assert out.json["preamble"] == preamble
    kwargs = send.await_args.kwargs
    assert kwargs["sidecar_content"] == render_preamble_markdown(
        preamble, thread_id="16416"
    )
    assert len(kwargs["sidecar_content"]) > RESIDUE_CAP_CHARS
    residue_block = kwargs["body"].split("## Residue")[1].split("## Anchor")[0]
    assert len(residue_block) < RESIDUE_CAP_CHARS
    assert "Preamble: see checkpoint sidecar" in residue_block


@pytest.mark.asyncio
async def test_post_no_stale_preamble_carry_forward() -> None:
    """Second checkpoint without preamble must not re-send prior sidecar."""
    ctx = _Ctx()
    ctx.outputs["pre_consolidate"] = {
        "json": {
            "residue": "Mission: only residue",
            "residue_source": "mechanical",
            "mission": "resume",
            "executor": "skipped",
        }
    }
    send = AsyncMock(return_value=({"turn_number": 12}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 11}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        await handler.execute(_Step(), ctx)
    assert send.await_args.kwargs.get("sidecar_content") is None


@pytest.mark.asyncio
async def test_post_omits_sidecar_when_preamble_absent() -> None:
    ctx = _Ctx()
    ctx.outputs["pre_consolidate"] = {
        "json": {
            "residue": "Mission: mechanical only",
            "residue_source": "mechanical",
            "mission": "resume",
            "executor": "skipped",
        }
    }
    send = AsyncMock(return_value=({"turn_number": 11}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        await handler.execute(_Step(), ctx)
    kwargs = send.await_args.kwargs
    assert kwargs.get("sidecar_content") is None
    assert normalize_preamble(ctx.outputs["pre_consolidate"]["json"]) is None
