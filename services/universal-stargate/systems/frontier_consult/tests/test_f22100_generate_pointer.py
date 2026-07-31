"""Friction 22100 — op=generate pointer turn must be a reference envelope.

Both generate admission paths (API-role and cursor-sdk no-packet) previously
seeded the auto-provisioned result thread's turn 1 with ``last_user[:2000]`` —
a raw, mid-word-truncated prompt copy with no back-link. These tests pin the
fix: turn 1 is a short reference envelope citing the dispatch thread id and a
correlation id, with NO prompt copy, produced by the shared helper
``build_generate_dispatch_pointer``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response

from systems.frontier_consult.handoff import (
    build_generate_dispatch_pointer,
    extract_generate_pointer_summary,
)

LONG_PROMPT = (
    "Investigate the dispatch pointer truncation defect.\n\n"
    + ("Repeated detail sentence for prompt padding. " * 200)
)
assert len(LONG_PROMPT) > 5000


def test_f22100_build_generate_dispatch_pointer_reference_envelope() -> None:
    body = build_generate_dispatch_pointer(
        lane="synthesizer",
        contract="light-bounded",
        dispatch_thread_id="4116",
        correlation_id="req-abc123",
        summary="Investigate the dispatch pointer truncation defect.",
    )
    assert "4116" in body
    assert "req-abc123" in body
    assert "agent_bus(get" in body
    assert len(body) < 500
    # No mid-word truncation: every line ends cleanly (no bare cut)
    for line in body.splitlines():
        assert not line.endswith("-"), f"suspicious mid-word cut: {line!r}"
    assert LONG_PROMPT[:2000] not in body


def test_f22100_pointer_summary_is_single_bounded_line() -> None:
    summary = extract_generate_pointer_summary(LONG_PROMPT)
    assert summary == "Investigate the dispatch pointer truncation defect."
    long_line = "word " * 100
    clipped = extract_generate_pointer_summary(long_line)
    assert clipped is not None
    assert len(clipped) <= 161
    assert "\n" not in clipped
    assert extract_generate_pointer_summary("\n\n  \n") is None


def test_f22100_both_lanes_structurally_equivalent() -> None:
    api = build_generate_dispatch_pointer(
        lane="reviewer",
        contract="light-bounded",
        dispatch_thread_id="4116",
        correlation_id="req-1",
        summary="s",
    )
    sdk = build_generate_dispatch_pointer(
        lane="SDK",
        contract="light-bounded",
        dispatch_thread_id="4116",
        correlation_id="exec-1",
        summary="s",
    )
    for body, corr in ((api, "req-1"), (sdk, "exec-1")):
        assert "4116" in body
        assert corr in body
        assert "Summary: s" in body
        assert "agent_bus(get" in body


@pytest.mark.asyncio
async def test_f22100_api_role_generate_pointer_is_reference_not_truncation() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        role="synthesizer",
        dispatch_thread_id="4116",
        contract="light-bounded",
        model="anthropic/claude-sonnet-4-6",
        caller_agent="claude-web",
    )
    response = Response()
    dispatch_payload = {
        "execution_id": "exec-f22100",
        "status": "running",
        "knob_resolution": {},
        "capabilities": {},
    }
    mock_profile = type("Profile", (), {"tool_surface": "mcp"})()

    with (
        patch(
            "systems.frontier_consult.api_role_generate.create_handoff_thread",
            new_callable=AsyncMock,
            return_value="4117",
        ) as create_thread,
        patch(
            "systems.frontier_consult.route._dispatch",
            new_callable=AsyncMock,
            return_value=dispatch_payload,
        ),
        patch(
            "systems.frontier_consult.api_role_generate._resolve_role_profile",
            return_value=("synthesizer", "anthropic", "api", mock_profile),
        ),
        patch(
            "systems.frontier_consult.api_role_generate.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
            return_value=LONG_PROMPT,
        ),
    ):
        from systems.frontier_consult.api_role_generate import (
            dispatch_api_role_generate,
        )

        await dispatch_api_role_generate(
            request_id="req-f22100",
            body=body,
            response=response,
        )

    pointer_body = create_thread.await_args.kwargs["pointer_body"]
    assert pointer_body != LONG_PROMPT[:2000]
    assert LONG_PROMPT[1500:2000] not in pointer_body, "prompt tail leaked"
    assert "4116" in pointer_body
    assert "req-f22100" in pointer_body
    assert len(pointer_body) < 500


@pytest.mark.asyncio
async def test_f22100_cursor_sdk_no_packet_pointer_matches_api_role_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult.cursor_sdk_generate import (
        dispatch_cursor_sdk_generate,
    )

    for name in (
        "emit_sdk_generate_requested",
        "emit_sdk_thread_created",
        "emit_sdk_worker_outcome",
    ):
        monkeypatch.setattr(
            f"systems.frontier_consult.cursor_sdk_generate.{name}",
            lambda **_kw: None,
        )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.admit_handoff_dispatch",
        AsyncMock(),
    )
    worker = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate."
        "dispatch_cursor_sdk_worker_message",
        worker,
    )
    captured: list[dict] = []

    async def _capture_create(**kwargs):
        captured.append(kwargs)
        return "4117"

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.create_handoff_thread",
        _capture_create,
    )

    result = await dispatch_cursor_sdk_generate(
        request_id="req-f22100-sdk",
        role="cursor-sdk",
        contract="light-bounded",
        model=None,
        subject="f22100 sdk pointer",
        caller_agent="claude-web",
        packet_path=None,
        message_text=LONG_PROMPT,
        reuse_thread=None,
        dispatch_thread_id="4116",
    )

    assert len(captured) == 1
    pointer_body = captured[0]["pointer_body"]
    execution_id = result["execution_id"]
    assert pointer_body != LONG_PROMPT[:2000]
    assert LONG_PROMPT[1500:2000] not in pointer_body, "prompt tail leaked"
    assert "4116" in pointer_body
    assert execution_id in pointer_body
    assert len(pointer_body) < 500
    # Worker still receives the FULL prompt — truncation was provenance-only.
    assert worker.await_args.kwargs["message"] == LONG_PROMPT
