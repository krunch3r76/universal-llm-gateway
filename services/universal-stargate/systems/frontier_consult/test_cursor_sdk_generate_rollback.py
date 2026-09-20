"""Rollback dispatch links when GIW refuses after bus dispatch-admit."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from .cursor_sdk_generate import _finish_prepared_dispatch
from .cursor_sdk_prepared_handle import PreparedCursorSdkHandle


def _handle(**overrides: object) -> PreparedCursorSdkHandle:
    base = {
        "request_id": "req-1",
        "execution_id": "exec-1",
        "dispatch_id": "disp-1",
        "thread_id": "11794",
        "resolved_model": "composer-2.5",
        "role": "cursor-sdk",
        "family": "cursor",
        "platform": "cursor",
        "to_agent": "cursor-sdk",
        "handoff_contract": "conductor",
        "packet_path": "/tmp/packet.md",
        "message": None,
        "caller_agent": "conductor-hop",
        "read_only": True,
        "aligned_knobs": None,
        "prompt_preamble": None,
        "thread_subject": "nested",
        "pointer_body": "go",
        "effective_bus_lifecycle": "ephemeral",
        "parent_dispatch_thread_id": "11788",
        "dispatch_thread_id": "11788",
        "density_triage": None,
        "review_opt_out_reason_code": None,
        "auto_review_child": False,
        "auto_review_defaulted": False,
        "claimed_via_atomic": False,
        "admitted": True,
        "alignment_warnings": (),
        "knob_resolution": (),
        "lane": "B",
    }
    base.update(overrides)
    return PreparedCursorSdkHandle(**base)


@pytest.mark.asyncio
async def test_worker_refusal_rolls_back_worker_and_parent_links() -> None:
    handle = _handle()
    worker_detail = {
        "status_code": 422,
        "code": "CURSOR_LANE_B_READ_ONLY",
        "message": "read_only=true is incompatible with lane='B'",
    }
    from .admission import FrontierEndpointError

    with (
        patch(
            "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker",
            new=AsyncMock(return_value=(False, worker_detail)),
        ),
        patch(
            "systems.frontier_consult.handoff.rollback_admitted_dispatch_links",
            new=AsyncMock(),
        ) as rollback,
        patch(
            "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        ),
        pytest.raises(FrontierEndpointError),
    ):
        await _finish_prepared_dispatch(handle)

    rollback.assert_awaited_once_with(
        request_id="req-1",
        worker_thread_id="11794",
        execution_id="exec-1",
        parent_dispatch_thread_id="11788",
    )
