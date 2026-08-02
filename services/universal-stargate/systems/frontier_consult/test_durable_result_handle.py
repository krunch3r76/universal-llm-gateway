"""Tests for SDK/API generate result_handle.durable honesty."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .handoff_response import (
    build_handoff_result,
    build_poll_hint_wait,
    build_result_handle,
    build_sdk_generate_result,
    resolve_poll_wait_seconds,
)
from .poll_hint_events import (
    FrontierPollHintIssued,
    emit_poll_hint_from_handoff,
    emit_poll_hint_issued,
)


def _mock_profile() -> MagicMock:
    profile = MagicMock()
    profile.tool_surface = "sdk"
    return profile


def test_build_handoff_result_surfaces_reply_from_agent() -> None:
    r1 = build_handoff_result(
        thread_id="t-explicit",
        to_agent="reviewer",
        reply_from_agent="cursor-sdk",
    )
    assert r1["reply_from_agent"] == "cursor-sdk"
    assert r1["reply_from_agent"] == r1["poll_hint"]["arguments"]["from_agent"]

    r2 = build_handoff_result(thread_id="t-default", to_agent="reviewer")
    assert r2["reply_from_agent"] == "reviewer"
    assert r2["reply_from_agent"] == r2["poll_hint"]["arguments"]["from_agent"]


def test_build_result_handle_after_turn_default_and_override() -> None:
    default = build_result_handle(thread_id="t1")
    assert default["after_turn"] == 1
    custom = build_result_handle(thread_id="t1", after_turn=4)
    assert custom["after_turn"] == 4


def test_resolve_poll_wait_seconds_seat_aware() -> None:
    # Default web/API poller keeps the 300s server-side block (a:5129 ceiling).
    assert resolve_poll_wait_seconds() == 300
    assert resolve_poll_wait_seconds(caller_agent="claude-web") == 300
    assert resolve_poll_wait_seconds(caller_agent="web-anthropic") == 300
    assert resolve_poll_wait_seconds(caller_agent=None) == 300
    # Any Cursor-IDE-platform seat gets a 0s snapshot (friction 24081 / Fable F1).
    assert resolve_poll_wait_seconds(caller_agent="cursor") == 0
    assert resolve_poll_wait_seconds(caller_agent="Cursor") == 0
    assert resolve_poll_wait_seconds(caller_agent="claude-cursor") == 0
    assert resolve_poll_wait_seconds(caller_agent="gpt-cursor") == 0
    assert resolve_poll_wait_seconds(poller_is_cursor_ide=True) == 0
    # poller_is_cursor_ide forces snapshot regardless of caller_agent.
    assert (
        resolve_poll_wait_seconds(caller_agent="claude-web", poller_is_cursor_ide=True)
        == 0
    )


def test_build_poll_hint_wait_respects_wait_seconds() -> None:
    default = build_poll_hint_wait(thread_id="t9", from_agent="reviewer")
    assert default["arguments"]["wait_seconds"] == 300
    snap = build_poll_hint_wait(thread_id="t9", from_agent="reviewer", wait_seconds=0)
    assert snap["arguments"]["wait_seconds"] == 0
    # arguments_json (the MCP wire form) must carry the same value.
    assert '"wait_seconds":0' in snap["arguments_json"]


def test_build_handoff_result_forwards_poll_wait_seconds() -> None:
    web = build_handoff_result(thread_id="t10", to_agent="reviewer")
    assert web["poll_hint"]["arguments"]["wait_seconds"] == 300
    cursor = build_handoff_result(
        thread_id="t10",
        to_agent="cursor-sdk",
        poll_wait_seconds=resolve_poll_wait_seconds(poller_is_cursor_ide=True),
    )
    assert cursor["poll_hint"]["arguments"]["wait_seconds"] == 0


def test_build_poll_hint_wait_after_turn_default_and_override() -> None:
    default = build_poll_hint_wait(thread_id="t2", from_agent="reviewer")
    assert default["arguments"]["after_turn"] == 1
    custom = build_poll_hint_wait(
        thread_id="t2", from_agent="reviewer", after_turn=3
    )
    assert custom["arguments"]["after_turn"] == 3


def test_build_handoff_result_threads_after_turn() -> None:
    result = build_handoff_result(
        thread_id="t3", to_agent="synthesizer", after_turn=5
    )
    assert result["result_handle"]["after_turn"] == 5
    assert result["poll_hint"]["arguments"]["after_turn"] == 5


def test_sdk_result_handle_durable_when_admit_succeeded() -> None:
    handoff_fields = build_handoff_result(
        thread_id="2015",
        to_agent="cursor-sdk:dispatch:exec-1",
        reply_from_agent="cursor-sdk",
    )
    result = build_sdk_generate_result(
        role="cursor-sdk",
        profile=_mock_profile(),
        handoff_fields=handoff_fields,
        execution_id="exec-1",
        thread_id="2015",
        to_agent="cursor-sdk:dispatch:exec-1",
        resolved_model="composer-2.5-fast",
        resolved_contract="implement",
        warnings=[],
        durable=True,
    )
    assert result["result_handle"]["durable"] is True
    assert result["result_handle"]["execution_id"] == "exec-1"


def test_sdk_result_handle_not_durable_when_link_admit_skipped() -> None:
    handoff_fields = build_handoff_result(
        thread_id="2016",
        to_agent="cursor-sdk:dispatch:exec-2",
        reply_from_agent="cursor-sdk",
    )
    result = build_sdk_generate_result(
        role="cursor-sdk",
        profile=_mock_profile(),
        handoff_fields=handoff_fields,
        execution_id="exec-2",
        thread_id="2016",
        to_agent="cursor-sdk:dispatch:exec-2",
        resolved_model="composer-2.5-fast",
        resolved_contract="implement",
        warnings=[],
        durable=False,
    )
    assert result["result_handle"]["durable"] is False


def test_sdk_bus_thread_delivery_unchanged() -> None:
    handoff_fields = build_handoff_result(
        thread_id="2017",
        to_agent="cursor-sdk:dispatch:exec-3",
        reply_from_agent="cursor-sdk",
    )
    result = build_sdk_generate_result(
        role="cursor-sdk",
        profile=_mock_profile(),
        handoff_fields=handoff_fields,
        execution_id="exec-3",
        thread_id="2017",
        to_agent="cursor-sdk:dispatch:exec-3",
        resolved_model="composer-2.5-fast",
        resolved_contract="implement",
        warnings=[],
        durable=True,
    )
    assert result["poll_hint"]["tool"] == "wait"
    assert result["poll_hint"]["arguments"]["thread"] == "2017"
    assert result["poll_hint"]["arguments"]["from_agent"] == "cursor-sdk"
    assert result["thread_id"] == "2017"
    assert result["output_contract"] == "thread"


@pytest.mark.asyncio
async def test_admit_handoff_dispatch_returns_false_when_token_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "true")

    from .handoff import admit_handoff_dispatch

    admitted = await admit_handoff_dispatch(
        request_id="req-skip",
        thread_id="2018",
        execution_id="exec-skip",
        pipeline_id="cursor-sdk-generate",
        caller_agent="dispatch",
    )
    assert admitted is False


@pytest.mark.asyncio
async def test_admit_handoff_dispatch_returns_true_on_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    from .handoff import admit_handoff_dispatch

    with patch(
        "systems.frontier_consult.handoff.make_async_client",
        return_value=mock_ctx,
    ):
        admitted = await admit_handoff_dispatch(
            request_id="req-ok",
            thread_id="2019",
            execution_id="exec-ok",
            pipeline_id="cursor-sdk-generate",
            caller_agent="dispatch",
        )
    assert admitted is True


def test_frontier_poll_hint_issued_factory_payload() -> None:
    event = FrontierPollHintIssued(
        request_id="req-1",
        thread_id="5020",
        caller_agent="cursor",
        wait_seconds=0,
        after_turn=1,
        reply_from_agent="cursor-sdk",
        issued_at="2026-07-12T00:00:00+00:00",
    )
    assert event.signal == "frontier.poll.hint.issued"
    assert event.payload == {
        "request_id": "req-1",
        "thread_id": "5020",
        "caller_agent": "cursor",
        "wait_seconds": 0,
        "after_turn": 1,
        "reply_from_agent": "cursor-sdk",
        "issued_at": "2026-07-12T00:00:00+00:00",
    }


def test_emit_poll_hint_issued_publishes_once() -> None:
    published: list[Any] = []

    with patch(
        "systems.frontier_consult.poll_hint_events.publish_frontier_event",
        side_effect=published.append,
    ):
        emit_poll_hint_issued(
            request_id="req-2",
            thread_id="5021",
            caller_agent="claude-web",
            wait_seconds=60,
            after_turn=2,
            reply_from_agent="reviewer",
            issued_at="2026-07-12T01:00:00+00:00",
        )

    assert len(published) == 1
    assert published[0].signal == "frontier.poll.hint.issued"
    assert published[0].payload["thread_id"] == "5021"


def test_emit_poll_hint_from_handoff_reads_build_handoff_result() -> None:
    handoff_fields = build_handoff_result(
        thread_id="5022",
        to_agent="reviewer",
        after_turn=3,
        poll_wait_seconds=60,
    )
    published: list[Any] = []

    with patch(
        "systems.frontier_consult.poll_hint_events.publish_frontier_event",
        side_effect=published.append,
    ):
        emit_poll_hint_from_handoff(
            request_id="req-3",
            thread_id="5022",
            caller_agent="claude-web",
            handoff_fields=handoff_fields,
        )

    assert len(published) == 1
    payload = published[0].payload
    assert payload["wait_seconds"] == 60
    assert payload["after_turn"] == 3
    assert payload["reply_from_agent"] == "reviewer"


def test_probe_classify_hint_alert_when_no_wait_events() -> None:
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts.probes import poll_hint_wait_correlation as probe

    def _empty(_sql: str, _params: list[str], *, limit: int = 10_000) -> list[dict]:
        return []

    assert (
        probe.classify_hint(
            thread_id="5099",
            issued_ms=1_000_000,
            window_ms=300_000,
            query_fn=_empty,
        )
        == "alert"
    )


def test_probe_classify_hint_matched_on_wait_called() -> None:
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts.probes import poll_hint_wait_correlation as probe

    def _wait_called(
        sql: str, _params: list[str], *, limit: int = 10_000
    ) -> list[dict]:
        if "wait.called" in sql:
            return [{"1": 1}]
        return []

    assert (
        probe.classify_hint(
            thread_id="5100",
            issued_ms=2_000_000,
            window_ms=300_000,
            query_fn=_wait_called,
        )
        == "matched"
    )


def test_probe_find_alertable_hints_filters_matched() -> None:
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts.probes import poll_hint_wait_correlation as probe

    now_ms = 10_000_000
    window_ms = 300_000
    issued_ms = now_ms - window_ms - 1_000

    def _query(sql: str, params: list[str], *, limit: int = 10_000) -> list[dict]:
        if "frontier.poll.hint.issued" in sql:
            return [
                {
                    "seq": 1,
                    "ts_unix_ms": issued_ms,
                    "payload": json.dumps({"thread_id": "5101"}),
                }
            ]
        if "wait.called" in sql and params[-1] == "5101":
            return [{"1": 1}]
        return []

    alerts = probe.find_alertable_hints(
        now_ms=now_ms,
        window_s=300,
        lookback_s=86400,
        query_fn=_query,
    )
    assert alerts == []
