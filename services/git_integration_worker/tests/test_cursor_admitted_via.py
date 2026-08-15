"""Tests for ``admitted_via`` provenance on cursor-sdk dispatch."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from services.git_integration_worker.cursor_auto.nested_sdk import (
    submit_nested_dispatch,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _dispatch_record_json,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest
from services.git_integration_worker.routes.cursor_sdk import _maybe_emit_giw_dispatched


def test_admit_without_admitted_via_back_compat() -> None:
    req = CursorDispatchRequest(
        thread_id="5867",
        model="cursor/composer-2.5",
        dispatch_id="disp-bc",
        execution_id="exec-disp-bc",
        message="hello",
    )
    assert req.admitted_via is None


def test_unregistered_admitted_via_rejected() -> None:
    with pytest.raises(ValidationError):
        CursorDispatchRequest(
            thread_id="5867",
            model="cursor/composer-2.5",
            dispatch_id="disp-bad",
            execution_id="exec-disp-bad",
            message="hello",
            admitted_via="charter-runner",  # type: ignore[arg-type]
        )


def test_record_json_persists_admitted_via() -> None:
    req = CursorDispatchRequest(
        thread_id="5867",
        model="cursor/composer-2.5",
        dispatch_id="auto-rec1",
        execution_id="exec-auto-rec1",
        message="hello",
        admitted_via="cursor-auto",
    )
    data = json.loads(_dispatch_record_json(req))
    assert data["admitted_via"] == "cursor-auto"


@pytest.mark.asyncio
async def test_nested_submit_stamps_admitted_via_and_caller_agent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200
        content = b'{"admitted": true}'

        def json(self) -> dict[str, object]:
            return {"admitted": True}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, json: dict[str, object]) -> _Resp:
            captured.update(json)
            return _Resp()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient",
        lambda **_kw: _Client(),
    )

    job = AutoJob(
        job_id="j-nest",
        thread_id="5867",
        turn_number=1,
        subject="implement",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    await submit_nested_dispatch(
        job,
        model_id="composer-2.5",
        handoff_contract="implement",
        message="do work",
    )
    assert captured["admitted_via"] == "cursor-auto"
    assert captured["caller_agent"] == "web-anthropic"


@pytest.mark.asyncio
async def test_nested_from_agent_cursor_auto_does_not_set_caller_agent(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200
        content = b'{"admitted": true}'

        def json(self) -> dict[str, object]:
            return {"admitted": True}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, json: dict[str, object]) -> _Resp:
            captured.update(json)
            return _Resp()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient",
        lambda **_kw: _Client(),
    )

    job = AutoJob(
        job_id="j-corner",
        thread_id="5867",
        turn_number=1,
        subject="implement",
        body="TYPE: DIRECTIVE",
        from_agent="cursor-auto",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    await submit_nested_dispatch(
        job,
        model_id="composer-2.5",
        handoff_contract="implement",
        message="do work",
    )
    assert captured["admitted_via"] == "cursor-auto"
    assert "caller_agent" not in captured


@pytest.mark.asyncio
async def test_nested_submit_omits_lane_when_unset(monkeypatch) -> None:
    """AC-4: unspecified lane must not appear on the nested POST body."""
    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200
        content = b'{"admitted": true}'

        def json(self) -> dict[str, object]:
            return {"admitted": True}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, json: dict[str, object]) -> _Resp:
            captured.update(json)
            return _Resp()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient",
        lambda **_kw: _Client(),
    )

    job = AutoJob(
        job_id="j-lane-omit",
        thread_id="7224",
        turn_number=1,
        subject="implement",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    await submit_nested_dispatch(
        job,
        model_id="composer-2.5",
        handoff_contract="implement",
        message="do work",
    )
    assert "lane" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["A", "B"])
async def test_nested_submit_forwards_checkout_lane(monkeypatch, lane: str) -> None:
    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200
        content = b'{"admitted": true}'

        def json(self) -> dict[str, object]:
            return {"admitted": True}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, json: dict[str, object]) -> _Resp:
            captured.update(json)
            return _Resp()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient",
        lambda **_kw: _Client(),
    )

    job = AutoJob(
        job_id=f"j-lane-{lane}",
        thread_id="7224",
        turn_number=1,
        subject="implement",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
        lane=lane,
    )
    await submit_nested_dispatch(
        job,
        model_id="composer-2.5",
        handoff_contract="implement",
        message="do work",
    )
    assert captured["lane"] == lane


def test_load_promoted_request_recovers_admitted_via(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="5867",
        model="cursor/composer-2.5",
        dispatch_id="auto-promote",
        execution_id="exec-auto-promote",
        message="hello",
        admitted_via="cursor-auto",
    )
    fp = ledger.fingerprint(req)
    admission = MagicMock()
    ledger.admit(
        req=req,
        fingerprint=fp,
        execution_id=req.execution_id,
        caller_agent="web-anthropic",
        resolved_model="composer-2.5",
        admission=admission,
        read_only=True,
    )
    promoted = ledger.promote_next_queued(lease_key="unused", worker_instance="w1")
    if promoted is None:
        row = ledger.dispatch_status_by_thread(thread_id="5867")
        assert row is not None
        from services.git_integration_worker.cursor_dispatch_ledger import (
            PromotedDispatch,
        )

        promoted = PromotedDispatch(
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            execution_id=req.execution_id,
            caller_agent="web-anthropic",
            resolved_model="composer-2.5",
            source_repo=None,
            contract="consult",
            read_only=True,
            record_json=_dispatch_record_json(req),
        )
    loaded = ledger.load_promoted_request(promoted)
    assert loaded.admitted_via == "cursor-auto"


@pytest.mark.parametrize("admitted_via", [None, "stargate"])
def test_maybe_emit_giw_dispatched_skips_non_cursor_auto(
    admitted_via: str | None,
) -> None:
    req = CursorDispatchRequest(
        thread_id="5867",
        model="cursor/composer-2.5",
        dispatch_id="disp-skip",
        execution_id="exec-disp-skip",
        message="hello",
        admitted_via=admitted_via,  # type: ignore[arg-type]
    )
    with patch(
        "services.git_integration_worker.routes.cursor_sdk.emit_sdk_worker_dispatched",
    ) as emit_mock:
        _maybe_emit_giw_dispatched(req=req, packet_text="")
    emit_mock.assert_not_called()


def test_maybe_emit_giw_dispatched_emits_for_cursor_auto() -> None:
    req = CursorDispatchRequest(
        thread_id="5867",
        model="cursor/composer-2.5",
        dispatch_id="disp-nested",
        execution_id="exec-disp-nested",
        request_id="ledger-req-abc123",
        message="hello",
        admitted_via="cursor-auto",
    )
    with patch(
        "services.git_integration_worker.routes.cursor_sdk.emit_sdk_worker_dispatched",
    ) as emit_mock:
        _maybe_emit_giw_dispatched(req=req, packet_text="")
    emit_mock.assert_called_once_with(
        dispatch_id="disp-nested",
        thread_id="5867",
        execution_id="exec-disp-nested",
        request_id="ledger-req-abc123",
        admitted_via="cursor-auto",
        asked_by=emit_mock.call_args.kwargs["asked_by"],
        purpose=emit_mock.call_args.kwargs["purpose"],
        story_id=emit_mock.call_args.kwargs["story_id"],
    )
