"""L16 — restart-gate busy check scoped by work_key at sdk generate prepare."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from scripts.model_manager.ui.controller.intent_admit_fence import (
    queue_kw_for_pending_intent,
)
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_PENDING_DRAIN,
)

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cursor_sdk_generate_prepare_restart_busy import (
    busy_cursor_sdk_work_keys,
    enforce_restart_busy_scoped_by_work_key,
    raise_if_restart_busy_blocks_generate,
    restart_busy_should_defer_admit,
)


def _active_work(*work_keys: str) -> dict:
    return {
        "active_ops": [
            {
                "kind": "cursor_sdk",
                "route": "/api/v1/cursor/dispatch",
                "work_key": wk,
                "op_id": f"disp-{idx}",
            }
            for idx, wk in enumerate(work_keys)
        ]
    }


def test_busy_keys_extracted_from_active_ops() -> None:
    snap = _active_work("todo:a", "todo:b")
    assert busy_cursor_sdk_work_keys(snap) == frozenset({"todo:a", "todo:b"})


def test_different_work_key_does_not_defer() -> None:
    assert not restart_busy_should_defer_admit(
        admit_work_key="todo:review-k43",
        busy_work_keys=frozenset({"todo:land-stargate"}),
        restart_gate_active=True,
    )


def test_same_work_key_defers_once() -> None:
    assert restart_busy_should_defer_admit(
        admit_work_key="todo:land-stargate",
        busy_work_keys=frozenset({"todo:land-stargate"}),
        restart_gate_active=True,
    )


def test_unkeyed_admit_does_not_defer_at_stargate() -> None:
    assert not restart_busy_should_defer_admit(
        admit_work_key=None,
        busy_work_keys=frozenset({"todo:any"}),
        restart_gate_active=True,
    )


def test_nested_child_exempt_during_drain() -> None:
    assert not restart_busy_should_defer_admit(
        admit_work_key="todo:land-stargate",
        busy_work_keys=frozenset({"todo:land-stargate"}),
        restart_gate_active=True,
        nest_under="parent-dispatch-id",
    )


def test_resume_of_exempt_during_drain() -> None:
    assert not restart_busy_should_defer_admit(
        admit_work_key="todo:land-stargate",
        busy_work_keys=frozenset({"todo:land-stargate"}),
        restart_gate_active=True,
        resume_of="parked-parent",
    )


def test_inactive_restart_gate_never_defers() -> None:
    assert not restart_busy_should_defer_admit(
        admit_work_key="todo:same",
        busy_work_keys=frozenset({"todo:same"}),
        restart_gate_active=False,
    )


def test_raise_if_restart_busy_blocks_same_key() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        raise_if_restart_busy_blocks_generate(
            request_id="req-1",
            admit_work_key="todo:x",
            busy_work_keys=frozenset({"todo:x"}),
            restart_gate_active=True,
        )
    assert exc.value.status_code == 503
    assert exc.value.code == "restart_busy_deferred"


def test_queue_kw_unkeyed_fails_closed_on_blocked_lane() -> None:
    assert queue_kw_for_pending_intent(
        service="git_integration_worker",
        intent_status=STATUS_PENDING_DRAIN,
        occupied_lanes={"B"},
        admit_lane="B",
        admit_work_key=None,
        busy_work_keys=("todo:drain",),
    ) == {"queue_for_pending_intent": True}


def test_queue_kw_independent_work_key_bypasses_lane_queue() -> None:
    assert queue_kw_for_pending_intent(
        service="git_integration_worker",
        intent_status=STATUS_PENDING_DRAIN,
        occupied_lanes={"B"},
        admit_lane="B",
        admit_work_key="todo:other",
        busy_work_keys=("todo:drain",),
    ) == {}


def test_queue_kw_nest_under_bypasses_lane_queue() -> None:
    assert queue_kw_for_pending_intent(
        service="git_integration_worker",
        intent_status=STATUS_PENDING_DRAIN,
        occupied_lanes={"B"},
        admit_lane="B",
        nest_under="parent-id",
    ) == {}


@pytest.mark.asyncio
async def test_prepare_enforce_allows_different_work_key() -> None:
    await enforce_restart_busy_scoped_by_work_key(
        request_id="req-2",
        work_key="todo:independent-review",
        admit_provenance=None,
        active_work_loader=lambda: _active_work("todo:drain-holder"),
        intent_loader=lambda _svc: type(
            "I", (), {"status": STATUS_PENDING_DRAIN}
        )(),
    )


@pytest.mark.asyncio
async def test_prepare_enforce_defers_matching_work_key() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        await enforce_restart_busy_scoped_by_work_key(
            request_id="req-3",
            work_key="todo:drain-holder",
            admit_provenance=None,
            active_work_loader=lambda: _active_work("todo:drain-holder"),
            intent_loader=lambda _svc: type(
                "I", (), {"status": STATUS_PENDING_DRAIN}
            )(),
        )
    assert exc.value.code == "restart_busy_deferred"


@pytest.mark.asyncio
async def test_prepare_enforce_unkeyed_does_not_503_at_stargate() -> None:
    await enforce_restart_busy_scoped_by_work_key(
        request_id="req-4",
        work_key=None,
        admit_provenance=None,
        active_work_loader=lambda: _active_work("todo:busy"),
        intent_loader=lambda _svc: type(
            "I", (), {"status": STATUS_PENDING_DRAIN}
        )(),
    )


@pytest.mark.asyncio
async def test_prepare_enforce_fail_open_when_giw_unreachable() -> None:
    await enforce_restart_busy_scoped_by_work_key(
        request_id="req-5",
        work_key="todo:busy",
        admit_provenance=None,
        active_work_loader=lambda: None,
        intent_loader=lambda _svc: type(
            "I", (), {"status": STATUS_PENDING_DRAIN}
        )(),
    )


@pytest.mark.asyncio
async def test_prepare_enforce_skipped_when_intent_not_pending() -> None:
    await enforce_restart_busy_scoped_by_work_key(
        request_id="req-6",
        work_key="todo:busy",
        admit_provenance=None,
        active_work_loader=lambda: _active_work("todo:busy"),
        intent_loader=lambda _svc: type("I", (), {"status": "completed"})(),
    )


@pytest.mark.asyncio
async def test_prepare_cursor_sdk_generate_invokes_restart_busy_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod

    gate = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare_restart_busy."
        "enforce_restart_busy_scoped_by_work_key",
        gate,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.densify_triage.validate_generate_density_intake",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.admission.enforce_check_review_substrate_admission",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_lane_ac_observer."
        "prepare_lb_auto_review_for_generate",
        lambda **_k: (False, False, ""),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_lane_ac_observer."
        "stamp_lb_review_spawn_fields",
        lambda **_k: ("source", None, False),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_pool_fence."
        "reject_other_models_pool_generate",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_reasoning_effort_reject."
        "reject_nonempty_reasoning_effort",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_alignment.align_cursor_knobs",
        lambda **_k: object(),
    )
    monkeypatch.setattr(
        prepare_mod,
        "finish_cursor_sdk_generate_prepare",
        AsyncMock(return_value=object()),
    )

    with patch(
        "implement_admission.routing.load_route_policy",
        return_value=object(),
    ), patch(
        "implement_admission.workflow_registry.load_auto_judgment_default_model",
        return_value="cursor/auto",
    ):
        await prepare_mod.prepare_cursor_sdk_generate(
            request_id="req-prepare-gate",
            role="worker",
            model="cursor/auto",
            subject=None,
            caller_agent="cursor",
            contract="sketch",
            packet_path=None,
            message_text="hi",
            work_key="todo:gate-probe",
            nest_under="nest-disp",
        )

    gate.assert_awaited_once()
    kwargs = gate.await_args.kwargs
    assert kwargs["work_key"] == "todo:gate-probe"
    assert kwargs["nest_under"] == "nest-disp"
