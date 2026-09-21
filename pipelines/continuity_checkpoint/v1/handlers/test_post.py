"""Unit tests for ContinuityCheckpointPostHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ._card_patch import RESIDUE_CAP_CHARS, clamp_residue
from .post import (
    ContinuityCheckpointPostHandler,
    _carry_forward_residue,
    _carry_forward_scoreboard_pin,
    _compose_body,
)
from .tail_mechanical import run_tail_mechanical

pytestmark = pytest.mark.offline


def test_c2_carry_forward_residue_from_prior_tip() -> None:
    prior = (
        "## Residue (authored — cap ~800 chars)\n"
        "Settled: prior arc work shipped.\n"
        "Next: verify fold.\n"
    )
    carried = _carry_forward_residue(prior_body=prior, prior_turn=12)
    assert carried is not None
    assert "Settled: prior arc work shipped." in carried
    assert "(carried forward from turn 12 — pipeline produced no residue)" in carried


def test_c2_no_prior_residue_returns_none() -> None:
    assert _carry_forward_residue(prior_body="## Anchor\nno residue\n", prior_turn=1) is None


def test_compose_body_refused_seal_omits_window() -> None:
    body = _compose_body(
        residue="WIP",
        seal={"refused": {"code": "transcript_seal.not_lane_window"}},
        mission="resume",
    )
    assert "Window:" not in body
    assert "Harvest: refused(transcript_seal.not_lane_window)" in body


def test_compose_body_cursor_hop_channel_token() -> None:
    body = _compose_body(
        residue="WIP",
        seal={
            "transcript_id": "550e8400-e29b-41d4-a716-446655440000",
            "turn_count": 4,
            "session_id": "cursor-test",
            "messages_sha256": "deadbeef",
            "refused": None,
        },
        mission="resume",
        surface="cursor",
        channel="hop",
    )
    assert (
        "Window: transcript_id=550e8400-e29b-41d4-a716-446655440000 · "
        "turns@cp=4 · channel=hop"
    ) in body
    from cortex_store.transcript_projection_membership import _WINDOW_ANCHOR_RE

    for line in body.splitlines():
        if line.startswith("Window:"):
            assert _WINDOW_ANCHOR_RE.search(line) is not None


def test_compose_body_claude_ai_window_anchor() -> None:
    body = _compose_body(
        residue="WIP",
        seal={
            "transcript_id": "cse_abc123",
            "turn_count": 1,
            "session_id": "web-anthropic-test",
            "messages_sha256": "deadbeef",
            "chat_url": "https://claude.ai/cowork/cse_abc123",
            "coverage": "tail",
            "refused": None,
        },
        mission="resume",
        surface="claude_ai",
    )
    assert (
        "Window: chat_url=https://claude.ai/cowork/cse_abc123 · "
        "transcript_id=cse_abc123 · turns@cp=1 · coverage=tail"
    ) in body
    assert "messages_sha256:deadbeef" in body
    assert "surface:claude_ai" in body


def test_compose_body_claude_ai_tail_absent_hash() -> None:
    body = _compose_body(
        residue="WIP",
        seal={
            "transcript_id": "cse_abc123",
            "turn_count": 1,
            "session_id": "web-anthropic-test",
            "chat_url": "https://claude.ai/cowork/cse_abc123",
            "coverage": "tail",
            "refused": None,
        },
        mission="resume",
        surface="claude_ai",
    )
    assert "coverage=tail" in body
    assert "messages_sha256:absent" in body
    assert "messages_sha256: ·" not in body


def test_compose_body_success_anchor() -> None:
    body = _compose_body(
        residue="WIP",
        seal={
            "transcript_id": "uuid-1",
            "turn_count": 5,
            "session_id": "cursor-test",
            "messages_sha256": "deadbeef",
            "refused": None,
        },
        mission="ship 4b",
    )
    assert "Window: transcript_id=uuid-1 · turns@cp=5" in body
    assert "Harvest: transcript:cursor-test" in body


class _Ctx:
    execution_id = "exec-post"
    dispatch_thread_id = "10223"
    options = {"thread": "10223", "surface": "cursor", "from_agent": "continuity"}
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
        "pre_consolidate": {
            "json": {
                "residue": "Mission: test post",
                "mission": "test post",
                "executor": "cursor-sdk",
                "card_patch_applied": False,
            }
        },
    }


class _Step:
    pass


@pytest.mark.asyncio
async def test_post_emits_terminal_result() -> None:
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10}, 200)),
        ),
        patch(
            "handlers.post.bus_send",
            new=AsyncMock(return_value=({"turn_number": 11}, 201)),
        ),
    ):
        out = await handler.execute(_Step(), _Ctx())
    assert out.json["status"] == "posted"
    assert out.json["pre_consolidate"]["executor"] == "cursor-sdk"
    assert out.json["harvest_entity_id"] == "transcript:cursor-test"
    assert out.json["pre_consolidate"]["supersedes_tip"] is True


@pytest.mark.asyncio
async def test_post_skipped_seal_is_info_not_checkpoint() -> None:
    ctx = _Ctx()
    ctx.options = {
        "thread": "10534",
        "surface": "cursor",
        "from_agent": "cursor",
        "residue": "caller residue must not hollow-tip",
    }
    ctx.outputs = {
        "resolve": {
            "json": {
                "refused": {
                    "code": "checkpoint.window_unresolvable",
                    "message": "not a root",
                }
            }
        },
        "seal": {},
        "pre_consolidate": {"json": {}},
    }
    send = AsyncMock(return_value=({"turn_number": 2}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 1}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        out = await handler.execute(_Step(), ctx)
    assert out.json["pre_consolidate"]["executor"] == "skipped"
    assert out.json["pre_consolidate"]["supersedes_tip"] is False
    assert out.json["seal"]["refused"]["code"] == "checkpoint.window_unresolvable"
    subject = send.await_args.kwargs["subject"]
    assert subject.startswith("INFO —")
    body = send.await_args.kwargs["body"]
    assert "Harvest: refused(checkpoint.window_unresolvable)" in body
    assert "Window:" not in body


@pytest.mark.asyncio
async def test_b1_5_unresolved_body_matches_compose_and_skipped_event() -> None:
    """B1-5 — unresolved tail leaves compose byte-identical; skipped event carries reason."""
    with patch("agent_bus_store.events.publisher.emit") as emit:
        tail = run_tail_mechanical(
            thread="99999",
            options={},
            tip_body="TYPE: CHECKPOINT\nno scoreboard token\n",
            thread_tags=[],
        )
    assert tail["folded"] is False
    assert tail["reason"] == "scoreboard_unresolved"
    emit.assert_called_once()
    assert emit.call_args.args[1]["reason"] == "scoreboard_unresolved"

    ctx = _Ctx()
    ctx.outputs["tail_mechanical"] = {"json": tail}
    seal = ctx.outputs["seal"]["json"]
    pre = ctx.outputs["pre_consolidate"]["json"]
    expected = _compose_body(
        residue=pre["residue"],
        seal=seal,
        mission=pre["mission"],
    )
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
    assert send.await_args.kwargs["body"] == expected


@pytest.mark.asyncio
async def test_b3_1_carried_forward_residue_in_post() -> None:
    """B3-1 — prior tip residue carries forward with marker; supersedes_tip unchanged."""
    prior = (
        "## Residue (authored — cap ~800 chars)\n"
        "Settled: prior arc work shipped.\n"
        "Next: verify fold.\n"
    )
    ctx = _Ctx()
    ctx.outputs["pre_consolidate"] = {"json": {"residue_source": None}}
    ctx.outputs["tail_mechanical"] = {"json": {}}
    send = AsyncMock(return_value=({"turn_number": 11}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(
                return_value=({"turn_number": 12, "body": prior}, 200),
            ),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        out = await handler.execute(_Step(), ctx)
    body = send.await_args.kwargs["body"]
    assert "Settled: prior arc work shipped." in body
    assert "(carried forward from turn 12 — pipeline produced no residue)" in body
    assert out.json["pre_consolidate"]["residue_source"] == "carried_forward"
    assert out.json["pre_consolidate"]["supersedes_tip"] is True


@pytest.mark.asyncio
async def test_b3_2_mechanical_stub_when_no_prior_residue() -> None:
    """B3-2 — no prior residue emits the a:33299 mechanical stub unchanged."""
    ctx = _Ctx()
    ctx.outputs = {
        "seal": {"json": {"refused": {"code": "checkpoint.seal_skipped"}}},
        "pre_consolidate": {"json": {}},
        "tail_mechanical": {"json": {}},
    }
    send = AsyncMock(return_value=({"turn_number": 2}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(
                return_value=({"turn_number": 1, "body": "## Anchor\nno residue\n"}, 200),
            ),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        out = await handler.execute(_Step(), ctx)
    body = send.await_args.kwargs["body"]
    assert (
        "TYPE: CHECKPOINT · pipeline · seal refused or pre_consolidate skipped."
        in body
    )
    assert out.json["pre_consolidate"]["executor"] == "skipped"
    assert out.json["pre_consolidate"]["supersedes_tip"] is False


def test_carry_forward_scoreboard_pin_appends_marker() -> None:
    prior = (
        "## Anchor\n"
        "Scoreboard: cortex://notes/system/threads/5250-charter-scoreboard.md · sha256:7e01657c\n"
        "Window: transcript_id=uuid-1 · turns@cp=5\n"
    )
    carried = _carry_forward_scoreboard_pin(prior)
    assert carried == (
        "Scoreboard: cortex://notes/system/threads/5250-charter-scoreboard.md "
        "· sha256:7e01657c · carried"
    )


def test_carry_forward_scoreboard_pin_idempotent_on_carried() -> None:
    prior = (
        "## Anchor\n"
        "Scoreboard: cortex://notes/system/threads/5250-charter-scoreboard.md "
        "· sha256:7e01657c · carried\n"
    )
    carried = _carry_forward_scoreboard_pin(prior)
    assert carried is not None
    assert carried.count(" · carried") == 1


def test_carry_forward_scoreboard_pin_none_when_absent() -> None:
    assert _carry_forward_scoreboard_pin("## Anchor\nno pin\n") is None
    assert _carry_forward_scoreboard_pin("") is None


@pytest.mark.asyncio
async def test_live_scoreboard_pin_wins_over_prior_carried_line() -> None:
    """Live tail pin wins; prior Scoreboard line must not leak with · carried."""
    prior = (
        "## Residue (authored — cap ~800 chars)\nWIP\n\n## Anchor\n"
        "Scoreboard: cortex://notes/old-board.md · sha256:deadbeef\n"
    )
    live_pin = "Scoreboard: cortex://notes/live-board.md · sha256:abc12345"
    ctx = _Ctx()
    ctx.outputs["tail_mechanical"] = {"json": {"scoreboard_pin": live_pin}}
    send = AsyncMock(return_value=({"turn_number": 11}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10, "body": prior}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        await handler.execute(_Step(), ctx)
    body = send.await_args.kwargs["body"]
    assert live_pin in body
    assert "old-board.md" not in body
    assert " · carried" not in body


@pytest.mark.asyncio
async def test_carried_scoreboard_pin_when_tail_unresolved() -> None:
    """Prior Scoreboard line carries forward with exactly one · carried marker."""
    prior_pin = (
        "Scoreboard: cortex://notes/system/threads/5250-charter-scoreboard.md "
        "· sha256:7e01657c"
    )
    prior = f"## Residue (authored — cap ~800 chars)\nWIP\n\n## Anchor\n{prior_pin}\n"
    ctx = _Ctx()
    ctx.outputs["tail_mechanical"] = {"json": {}}
    ctx.outputs["score"] = {"json": {}}
    send = AsyncMock(return_value=({"turn_number": 11}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10, "body": prior}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        await handler.execute(_Step(), ctx)
    body = send.await_args.kwargs["body"]
    assert f"{prior_pin} · carried" in body
    assert body.count(" · carried") == 1


@pytest.mark.asyncio
async def test_carried_scoreboard_pin_idempotent_across_consecutive_misses() -> None:
    """Re-carrying an already-carried line must not stack · carried markers."""
    prior_pin = (
        "Scoreboard: cortex://notes/system/threads/5250-charter-scoreboard.md "
        "· sha256:7e01657c · carried"
    )
    prior = f"## Residue (authored — cap ~800 chars)\nWIP\n\n## Anchor\n{prior_pin}\n"
    ctx = _Ctx()
    ctx.outputs["tail_mechanical"] = {"json": {}}
    ctx.outputs["score"] = {"json": {}}
    send = AsyncMock(return_value=({"turn_number": 11}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10, "body": prior}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        await handler.execute(_Step(), ctx)
    body = send.await_args.kwargs["body"]
    assert prior_pin in body
    assert body.count(" · carried") == 1


@pytest.mark.asyncio
async def test_no_scoreboard_pin_when_prior_and_tail_both_absent() -> None:
    """No Scoreboard line when tail and prior both lack a pin."""
    prior = "## Residue (authored — cap ~800 chars)\nWIP\n\n## Anchor\nno pin\n"
    ctx = _Ctx()
    ctx.outputs["tail_mechanical"] = {"json": {}}
    ctx.outputs["score"] = {"json": {}}
    send = AsyncMock(return_value=({"turn_number": 11}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10, "body": prior}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        await handler.execute(_Step(), ctx)
    body = send.await_args.kwargs["body"]
    assert "Scoreboard:" not in body


@pytest.mark.asyncio
async def test_model_residue_wins_over_caller_residue() -> None:
    """Worker residue (residue_source=model) wins over caller-authored seed."""
    ctx = _Ctx()
    ctx.options["residue"] = "caller seed must not win"
    ctx.outputs["pre_consolidate"]["json"]["residue_source"] = "model"
    ctx.outputs["pre_consolidate"]["json"]["residue"] = "Mission: worker wins"
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
    body = send.await_args.kwargs["body"]
    assert "Mission: worker wins" in body
    assert "caller seed must not win" not in body
    assert out.json["pre_consolidate"]["residue_source"] == "model"


@pytest.mark.asyncio
async def test_post_clamps_oversized_model_residue() -> None:
    """Post-side clamp applies marker and sets residue_clamped on terminal result."""
    long_residue = "Mission: ok\n" + ("y" * 930)
    ctx = _Ctx()
    ctx.outputs["pre_consolidate"]["json"]["residue"] = long_residue
    ctx.outputs["pre_consolidate"]["json"]["residue_source"] = "model"
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
    clamped, _ = clamp_residue(long_residue)
    body = send.await_args.kwargs["body"]
    assert clamped in body
    assert out.json["pre_consolidate"]["residue_clamped"] is True
    assert len(clamped) <= RESIDUE_CAP_CHARS
    assert len(clamped) >= int(RESIDUE_CAP_CHARS * 0.6)


@pytest.mark.asyncio
async def test_carried_scoreboard_pin_does_not_append_fold_rows() -> None:
    """Carried pin must not drag stale G-row fold lines from a prior tail."""
    prior_pin = (
        "Scoreboard: cortex://notes/system/threads/5250-charter-scoreboard.md "
        "· sha256:7e01657c"
    )
    prior = (
        f"## Residue (authored — cap ~800 chars)\nWIP\n\n## Anchor\n{prior_pin}\n"
        "| G1 | stale fold row |\n"
    )
    ctx = _Ctx()
    ctx.outputs["tail_mechanical"] = {
        "json": {"fold_row_lines": ["| G2 | would append if live |"]}
    }
    ctx.outputs["score"] = {"json": {}}
    send = AsyncMock(return_value=({"turn_number": 11}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10, "body": prior}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        await handler.execute(_Step(), ctx)
    body = send.await_args.kwargs["body"]
    assert f"{prior_pin} · carried" in body
    assert "| G2 |" not in body


@pytest.mark.asyncio
async def test_charter_pin_without_fold_does_not_append_g_rows() -> None:
    """Charter projection_only emits a pin + fold lines; only folded=True appends."""
    pin = (
        "Scoreboard: cortex://notes/system/threads/10479-charter-scoreboard.md "
        "· sha256:bdbe3629"
    )
    ctx = _Ctx()
    ctx.outputs["tail_mechanical"] = {
        "json": {
            "folded": False,
            "scoreboard_pin": pin,
            "fold_row_lines": ["| G1 | must not land in authored residue |"],
        }
    }
    ctx.outputs["score"] = {"json": {}}
    send = AsyncMock(return_value=({"turn_number": 11}, 201))
    handler = ContinuityCheckpointPostHandler()
    with (
        patch(
            "handlers.post.bus_get",
            new=AsyncMock(return_value=({"turn_number": 10, "body": ""}, 200)),
        ),
        patch("handlers.post.bus_send", new=send),
    ):
        await handler.execute(_Step(), ctx)
    body = send.await_args.kwargs["body"]
    assert pin in body
    assert "| G1 |" not in body
