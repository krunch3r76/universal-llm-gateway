"""Regression: cursor-sdk implement-generate admission (messages-fold).

Pins the behavior verified live on 2026-06-13 (agent-bus thread 1724; the live
symptom was a deploy-lag, not a code defect). The route's cursor-sdk generate
intercept must:

- ADMIT ``contract="implement"`` with a ``packet_path`` and NO caller-supplied
  messages, routing to the SDK orchestrator;
- NOT read the dispatch thread for implement (the implement corpus is the packet,
  not assembled dispatch-thread message text);
- still require caller-owned dispatch-thread context for non-implement contracts;
- never accept a public ``messages[]`` field on the folded generate wire.

Guards friction 17195 / assertion 17200. See
cortex:notes/system/threads/1724-messages-fold-implement-densify-findings.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from implement_admission.preflight import DecisionNotAssertedError
from pydantic import ValidationError

from .generate_wrap import GenerateWrapResult
from .route import TeamDispatchGenerateBody, team_dispatch


def _patch_sdk_and_thread_read(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sdk_return: dict[str, str],
    thread_body: str,
) -> tuple[AsyncMock, AsyncMock]:
    """Patch the SDK orchestrator + dispatch-thread reader on the route module."""
    sdk_mock = AsyncMock(return_value=sdk_return)
    thread_read = AsyncMock(return_value=thread_body)
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate", sdk_mock
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.read_latest_dispatch_thread_body",
        thread_read,
    )
    return sdk_mock, thread_read


@pytest.mark.asyncio
async def test_cursor_sdk_implement_admits_without_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """implement + packet_path admits via the SDK orchestrator; thread never read."""
    sdk_mock, thread_read = _patch_sdk_and_thread_read(
        monkeypatch,
        sdk_return={"execution_id": "exec-1", "thread_id": "1726"},
        thread_body="should-not-be-read",
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="implement",
        lane="B",
        packet_path="tmp/reviews/packet.md",
    )
    result = await team_dispatch(body, Response())

    assert result == {"execution_id": "exec-1", "thread_id": "1726"}
    # The implement corpus is the packet — the dispatch thread is never read,
    # so no "user message required" gate can fire on this path.
    thread_read.assert_not_awaited()
    sdk_mock.assert_awaited_once()
    kwargs = sdk_mock.await_args.kwargs
    assert kwargs["contract"] == "implement"
    assert kwargs["packet_path"] == "tmp/reviews/packet.md"
    assert kwargs["message_text"] == ""  # source_text="" for implement
    assert kwargs["parent_dispatch_thread_id"] == "todo:some-arc"
    assert kwargs.get("reuse_thread") is None
    assert kwargs.get("bus_lifecycle") is None  # defaults ephemeral inside orchestrator


@pytest.mark.asyncio
async def test_cursor_sdk_light_bounded_packet_skips_dispatch_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """light-bounded + packet_path admits via packet channel; thread never read."""
    sdk_mock, thread_read = _patch_sdk_and_thread_read(
        monkeypatch,
        sdk_return={"execution_id": "exec-light", "thread_id": "1730"},
        thread_body="should-not-be-read",
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap._resolve_packet_file",
        lambda _root, _path: __import__("pathlib").Path("/tmp/packet.md"),
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="light-bounded",
        lane="A",
        packet_path="tmp/reviews/light-packet.md",
    )
    result = await team_dispatch(body, Response())

    assert result == {"execution_id": "exec-light", "thread_id": "1730"}
    thread_read.assert_not_awaited()
    sdk_mock.assert_awaited_once()
    kwargs = sdk_mock.await_args.kwargs
    assert kwargs["contract"] == "light-bounded"
    assert kwargs["packet_path"] == "tmp/reviews/light-packet.md"
    assert kwargs["message_text"] == ""


@pytest.mark.asyncio
async def test_cursor_sdk_pure_mechanical_packet_skips_dispatch_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pure-mechanical + packet_path admits via packet channel; thread never read."""
    sdk_mock, thread_read = _patch_sdk_and_thread_read(
        monkeypatch,
        sdk_return={"execution_id": "exec-mech", "thread_id": "1731"},
        thread_body="should-not-be-read",
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap._resolve_packet_file",
        lambda _root, _path: __import__("pathlib").Path("/tmp/packet.md"),
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="pure-mechanical",
        lane="B",
        packet_path="tmp/reviews/mech-packet.md",
    )
    await team_dispatch(body, Response())

    thread_read.assert_not_awaited()
    sdk_mock.assert_awaited_once()
    assert sdk_mock.await_args.kwargs["packet_path"] == "tmp/reviews/mech-packet.md"
    assert sdk_mock.await_args.kwargs["message_text"] == ""


@pytest.mark.asyncio
async def test_cursor_sdk_light_bounded_unresolved_packet_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing packet_path on light generate → 422 packet_path_unresolved."""
    sdk_mock = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate", sdk_mock
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap._resolve_packet_file",
        lambda _root, _path: None,
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="light-bounded",
        lane="A",
        packet_path="tmp/missing.md",
    )
    result = await team_dispatch(body, Response())

    assert result.status_code == 422
    payload = result.body.decode()
    assert "packet_path_unresolved" in payload
    sdk_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_cursor_sdk_non_implement_reads_dispatch_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-implement still requires caller-owned context from the dispatch thread."""
    sdk_mock, thread_read = _patch_sdk_and_thread_read(
        monkeypatch,
        sdk_return={"execution_id": "exec-2", "thread_id": "1727"},
        thread_body="caller context",
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="light-bounded",
        lane="A",
    )
    await team_dispatch(body, Response())

    thread_read.assert_awaited_once()
    sdk_mock.assert_awaited_once()
    assert sdk_mock.await_args.kwargs["message_text"] == "caller context"


def test_team_generate_body_forbids_public_messages() -> None:
    """Folded wire: public messages[] must never be accepted on generate."""
    with pytest.raises(ValidationError):
        TeamDispatchGenerateBody(
            op="generate",
            seat="cursor-sdk",
            dispatch_thread_id="todo:some-arc",
            contract="implement",
            packet_path="tmp/reviews/packet.md",
            messages=[{"role": "user", "content": "x"}],  # type: ignore[call-arg]
        )


@pytest.mark.asyncio
@pytest.mark.offline
async def test_cursor_sdk_implement_admits_bare_source_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1: bare source_ref materializes and dispatches with the bridge packet_path."""
    sdk_mock = AsyncMock(
        return_value={"execution_id": "exec-wrap", "thread_id": "1728"}
    )
    thread_read = AsyncMock(return_value="should-not-be-read")
    gate_refs: list[str | None] = []
    bridge_refs: list[str] = []

    def _prepare(**kwargs: object) -> GenerateWrapResult:
        gate_refs.append(kwargs.get("source_ref"))  # type: ignore[arg-type]
        bridge_refs.append(kwargs.get("source_ref"))  # type: ignore[arg-type]
        return GenerateWrapResult(
            packet_path="tmp/reviews/first-class-wrap-transport-implement-packet.md",
            materialized=True,
            warnings=["executor-absent"],
        )

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate", sdk_mock
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.read_latest_dispatch_thread_body",
        thread_read,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet", _prepare
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="implement",
        lane="B",
        source_ref="todo:first-class-wrap-transport",
    )
    result = await team_dispatch(body, Response())

    assert result == {
        "execution_id": "exec-wrap",
        "thread_id": "1728",
        "materialization_mode": "auto",
        "warnings": ["executor-absent"],
    }
    thread_read.assert_not_awaited()
    sdk_mock.assert_awaited_once()
    assert sdk_mock.await_args.kwargs["packet_path"] == (
        "tmp/reviews/first-class-wrap-transport-implement-packet.md"
    )
    assert gate_refs == ["todo:first-class-wrap-transport"]
    assert bridge_refs == ["todo:first-class-wrap-transport"]


@pytest.mark.asyncio
@pytest.mark.offline
async def test_cursor_sdk_implement_gated_source_ref_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3: bridge gated → 422 generate_source_ref_gated; SDK not awaited."""
    sdk_mock = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate", sdk_mock
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        lambda **kwargs: GenerateWrapResult(
            packet_path=None,
            gated=True,
            gated_reason="judgment_required",
        ),
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="implement",
        lane="B",
        source_ref="todo:gated-slug",
    )
    result = await team_dispatch(body, Response())

    assert result.status_code == 422
    payload = result.body.decode()
    assert "generate_source_ref_gated" in payload
    sdk_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.offline
async def test_cursor_sdk_implement_decision_not_asserted_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3a: un-ratified decision on materialization sub-path → 422."""
    sdk_mock = AsyncMock()

    def _raise_decision(**kwargs: object) -> GenerateWrapResult:  # noqa: ARG001
        raise DecisionNotAssertedError()

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate", sdk_mock
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        _raise_decision,
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="implement",
        lane="B",
        source_ref="todo:unratified",
    )
    result = await team_dispatch(body, Response())

    assert result.status_code == 422
    payload = result.body.decode()
    assert "decision_not_asserted" in payload
    sdk_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.offline
async def test_cursor_sdk_implement_packet_path_no_materialization_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-5: inline packet_path admits unchanged; no materialization_mode key."""
    sdk_mock, thread_read = _patch_sdk_and_thread_read(
        monkeypatch,
        sdk_return={"execution_id": "exec-inline", "thread_id": "1729"},
        thread_body="should-not-be-read",
    )
    prepare_calls: list[bool] = []

    def _track_prepare(**kwargs: object) -> GenerateWrapResult:
        prepare_calls.append(True)
        return GenerateWrapResult(packet_path=kwargs["packet_path"])  # type: ignore[arg-type]

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        _track_prepare,
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="todo:some-arc",
        contract="implement",
        lane="B",
        packet_path="tmp/reviews/packet.md",
    )
    result = await team_dispatch(body, Response())

    assert result == {"execution_id": "exec-inline", "thread_id": "1729"}
    assert "materialization_mode" not in result
    assert prepare_calls == [True]
    thread_read.assert_not_awaited()
    sdk_mock.assert_awaited_once()
    assert sdk_mock.await_args.kwargs["packet_path"] == "tmp/reviews/packet.md"
