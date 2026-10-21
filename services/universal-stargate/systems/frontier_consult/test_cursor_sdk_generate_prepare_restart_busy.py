"""L16 — restart-gate busy check scoped by work_key at sdk generate prepare."""

from __future__ import annotations

import pytest
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


def test_absent_work_key_global_defer_when_busy() -> None:
    assert restart_busy_should_defer_admit(
        admit_work_key=None,
        busy_work_keys=frozenset({"todo:any"}),
        restart_gate_active=True,
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
async def test_prepare_enforce_global_when_work_key_missing() -> None:
    with pytest.raises(FrontierEndpointError):
        await enforce_restart_busy_scoped_by_work_key(
            request_id="req-4",
            work_key=None,
            admit_provenance=None,
            active_work_loader=lambda: _active_work("todo:busy"),
            intent_loader=lambda _svc: type(
                "I", (), {"status": STATUS_PENDING_DRAIN}
            )(),
        )
