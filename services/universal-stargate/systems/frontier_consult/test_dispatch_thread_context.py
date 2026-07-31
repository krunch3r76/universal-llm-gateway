"""Unit tests for dispatch-thread prompt latch gates (friction 24391)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.dispatch_thread_context import (
    allowed_prompt_recipients,
    is_server_dispatch_turn_body,
    read_latest_dispatch_thread_body,
    resolve_generate_prompt_body,
)


def test_allowed_prompt_recipients_api_role() -> None:
    assert allowed_prompt_recipients("artisan") == frozenset({"artisan", "dispatch"})


def test_allowed_prompt_recipients_cursor_sdk_aliases() -> None:
    assert allowed_prompt_recipients("cursor-sdk") == frozenset(
        {"cursor-sdk", "dispatch", "cursor", "claude-cursor"}
    )


def test_is_server_dispatch_turn_body_pointer() -> None:
    body = (
        "artisan light-bounded generate dispatch — prompt on dispatch thread "
        "`5129` (correlation `cde567c3999a`).\n\nRead full prompt: …"
    )
    assert is_server_dispatch_turn_body(body) is True


def _mock_turns_response(turn: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"turns": [turn]}
    resp.text = ""
    return resp


async def _read_with_turn(turn: dict[str, Any], *, role: str) -> str:
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=_mock_turns_response(turn))
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    with patch(
        "systems.frontier_consult.dispatch_thread_context.make_async_client",
        return_value=mock_ctx,
    ):
        return await read_latest_dispatch_thread_body(
            request_id="req-24391",
            dispatch_thread_id="5129",
            role=role,
        )


@pytest.mark.asyncio
async def test_a24391_status_self_note_refused() -> None:
    """agent-bus:5129#17 shape — cursor→cursor DONE note must not latch."""
    with pytest.raises(FrontierEndpointError) as excinfo:
        await _read_with_turn(
            {
                "from": "cursor",
                "to": "cursor",
                "body": "DONE — Questions re-verified; Cover Letter uploaded.",
            },
            role="artisan",
        )
    assert excinfo.value.code == "dispatch_thread_latest_not_prompt"


@pytest.mark.asyncio
async def test_a24391_prior_role_reply_refused() -> None:
    """SF2 — from=role reply must not become the next prompt."""
    with pytest.raises(FrontierEndpointError) as excinfo:
        await _read_with_turn(
            {
                "from": "artisan",
                "to": "dispatch",
                "body": "Green to proceed to Review → Certify → Submit.",
            },
            role="artisan",
        )
    assert excinfo.value.code == "dispatch_thread_latest_not_prompt"


@pytest.mark.asyncio
async def test_role_addressed_brief_admits() -> None:
    body = "RE-PROMPT for cover umph rewrite. Full brief in sidecar."
    got = await _read_with_turn(
        {"from": "cursor", "to": "artisan", "body": body},
        role="artisan",
    )
    assert got == body


@pytest.mark.asyncio
async def test_to_dispatch_brief_admits() -> None:
    body = "Review this design packet."
    got = await _read_with_turn(
        {"from": "cursor", "to": "dispatch", "body": body},
        role="artisan",
    )
    assert got == body


@pytest.mark.asyncio
async def test_server_pointer_still_pointer_code() -> None:
    with pytest.raises(FrontierEndpointError) as excinfo:
        await _read_with_turn(
            {
                "from": "dispatch",
                "to": "artisan",
                "body": (
                    "artisan light-bounded generate dispatch — prompt on "
                    "dispatch thread `5129` (correlation `abc`).\n\nSummary: x"
                ),
            },
            role="artisan",
        )
    assert excinfo.value.code == "dispatch_thread_latest_is_pointer"


@pytest.mark.asyncio
async def test_cursor_sdk_alias_self_note_admits() -> None:
    body = "Light-bounded recon brief for cursor-sdk."
    got = await _read_with_turn(
        {"from": "cursor", "to": "cursor", "body": body},
        role="cursor-sdk",
    )
    assert got == body


@pytest.mark.asyncio
async def test_missing_to_refused() -> None:
    with pytest.raises(FrontierEndpointError) as excinfo:
        await _read_with_turn(
            {"from": "cursor", "body": "orphan prompt without to="},
            role="artisan",
        )
    assert excinfo.value.code == "dispatch_thread_latest_not_prompt"


@pytest.mark.asyncio
async def test_inline_prompt_bypasses_bad_latest_turn() -> None:
    """SF1: prompt= on admit avoids 422 when latest turn is a status note."""
    mock_client = MagicMock()
    mock_client.get = AsyncMock(
        side_effect=AssertionError("bus read must not run when prompt is set")
    )
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    with patch(
        "systems.frontier_consult.dispatch_thread_context.make_async_client",
        return_value=mock_ctx,
    ):
        got = await resolve_generate_prompt_body(
            request_id="req-sf1",
            role="artisan",
            dispatch_thread_id="5129",
            prompt="RE-PROMPT for cover umph rewrite.",
        )
    assert got == "RE-PROMPT for cover umph rewrite."


@pytest.mark.asyncio
async def test_sidecar_ref_bypasses_bus_read() -> None:
    with (
        patch(
            "systems.frontier_consult.dispatch_thread_context._read_schemed_prompt_file",
            return_value="Review the attached design.",
        ) as read_sidecar,
        patch(
            "systems.frontier_consult.dispatch_thread_context.read_latest_dispatch_thread_body",
            side_effect=AssertionError("bus read must not run for sidecar_ref"),
        ),
    ):
        got = await resolve_generate_prompt_body(
            request_id="req-sidecar",
            role="reviewer",
            dispatch_thread_id="5129",
            sidecar_ref="cortex://notes/system/threads/review.md",
        )
    assert got == "Review the attached design."
    read_sidecar.assert_called_once()


@pytest.mark.asyncio
async def test_multiple_explicit_prompt_sources_fail_closed() -> None:
    with pytest.raises(FrontierEndpointError) as excinfo:
        await resolve_generate_prompt_body(
            request_id="req-multiple",
            role="reviewer",
            dispatch_thread_id="5129",
            packet_path="tmp/review.md",
            prompt="Review this.",
        )
    assert excinfo.value.code == "multiple_prompt_sources"
