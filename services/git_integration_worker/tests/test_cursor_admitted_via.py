"""Tests for ``admitted_via`` provenance on cursor-sdk dispatch."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

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


def test_record_json_persists_workspace() -> None:
    req = CursorDispatchRequest(
        thread_id="37504",
        model="cursor/composer-2.5",
        dispatch_id="auto-ws",
        execution_id="exec-auto-ws",
        message="hello",
        workspace="cryptax",
    )
    data = json.loads(_dispatch_record_json(req))
    assert data["workspace"] == "cryptax"


def test_stamp_inherited_workspace_stays_off_record_and_wire() -> None:
    """a:37533 — inherit provenance is admit-event only, not a spoofable wire field."""
    req = CursorDispatchRequest(
        thread_id="37533",
        model="cursor/composer-2.5",
        dispatch_id="child-ws",
        execution_id="exec-child-ws",
        message="hello",
    )
    req.stamp_inherited_workspace("parent-ws", "cryptax")
    assert req.workspace == "cryptax"
    assert req.workspace_inherited_from == "parent-ws"
    data = json.loads(_dispatch_record_json(req))
    assert data["workspace"] == "cryptax"
    assert "workspace_inherited_from" not in data
    with pytest.raises(ValidationError):
        CursorDispatchRequest(
            thread_id="37533",
            model="cursor/composer-2.5",
            dispatch_id="spoof-ws",
            execution_id="exec-spoof-ws",
            message="hello",
            workspace_inherited_from="parent-ws",  # type: ignore[call-arg]
        )


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


def test_load_promoted_request_recovers_workspace(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="37504p",
        model="cursor/composer-2.5",
        dispatch_id="auto-ws-promo",
        execution_id="exec-auto-ws-promo",
        message="hello",
        workspace="cryptax",
        admitted_via="cursor-auto",
    )
    promoted = ledger.promote_next_queued(lease_key="unused", worker_instance="w1")
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
    assert loaded.workspace == "cryptax"


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
        topic=emit_mock.call_args.kwargs["topic"],
        nest_under=emit_mock.call_args.kwargs["nest_under"],
        packet_kind=emit_mock.call_args.kwargs["packet_kind"],
        model_knobs_requested=emit_mock.call_args.kwargs["model_knobs_requested"],
    )
