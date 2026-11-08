"""AC3: auto-minted worker threads close after pre-admission GIW refusal."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from .cursor_sdk_generate import _finish_prepared_dispatch
from .cursor_sdk_prepared_handle import PreparedCursorSdkHandle


def _minted_handle(**overrides: object) -> PreparedCursorSdkHandle:
    base = {
        "request_id": "req-pre",
        "execution_id": "exec-pre",
        "dispatch_id": "disp-pre",
        "thread_id": "22690",
        "resolved_model": "composer-2.5",
        "role": "cursor-sdk",
        "family": "cursor",
        "platform": "cursor",
        "to_agent": "cursor-sdk",
        "handoff_contract": "implement",
        "packet_path": "/tmp/packet.md",
        "message": None,
        "caller_agent": "frontier-admit",
        "read_only": False,
        "aligned_knobs": None,
        "prompt_preamble": None,
        "thread_subject": "worker",
        "pointer_body": "go",
        "effective_bus_lifecycle": "ephemeral",
        "parent_dispatch_thread_id": None,
        "dispatch_thread_id": "22704",
        "density_triage": None,
        "review_opt_out_reason_code": None,
        "auto_review_child": False,
        "auto_review_defaulted": False,
        "claimed_via_atomic": False,
        "admitted": True,
        "alignment_warnings": (),
        "knob_resolution": (),
        "lane": "B",
        "worker_thread_reused": False,
    }
    base.update(overrides)
    return PreparedCursorSdkHandle(**base)


@pytest.mark.asyncio
async def test_pre_admission_refusal_closes_auto_minted_thread() -> None:
    """409 admission refusal closes minted worker thread after FAILED| turn."""
    worker_detail = {
        "status_code": 409,
        "code": "CURSOR_SOURCE_REF_IN_FLIGHT",
        "message": "source ref in flight",
        "failure_layer": "admission",
        "validation_stage": "pre_admission",
    }
    from .admission import FrontierEndpointError

    close_mock = AsyncMock()
    with (
        patch(
            "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker",
            new=AsyncMock(return_value=(False, worker_detail)),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.rollback_admitted_dispatch_links",
            new=AsyncMock(),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.post_worker_failure_turn",
            new=AsyncMock(),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.close_worker_thread_after_pre_admission_refusal",
            close_mock,
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        ),
        pytest.raises(FrontierEndpointError),
    ):
        await _finish_prepared_dispatch(_minted_handle())

    close_mock.assert_awaited_once_with(
        request_id="req-pre",
        thread_id="22690",
        worker_detail=worker_detail,
    )


@pytest.mark.asyncio
async def test_pre_admission_refusal_leaves_reused_thread_open() -> None:
    """Caller-supplied reuse_thread must not trigger post-FAILED close."""
    worker_detail = {
        "status_code": 409,
        "code": "CURSOR_SOURCE_REF_IN_FLIGHT",
        "message": "source ref in flight",
        "failure_layer": "admission",
    }
    from .admission import FrontierEndpointError

    close_mock = AsyncMock()
    with (
        patch(
            "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker",
            new=AsyncMock(return_value=(False, worker_detail)),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.rollback_admitted_dispatch_links",
            new=AsyncMock(),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.post_worker_failure_turn",
            new=AsyncMock(),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.close_worker_thread_after_pre_admission_refusal",
            close_mock,
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        ),
        pytest.raises(FrontierEndpointError),
    ):
        await _finish_prepared_dispatch(
            _minted_handle(thread_id="900", worker_thread_reused=True)
        )

    close_mock.assert_not_awaited()
