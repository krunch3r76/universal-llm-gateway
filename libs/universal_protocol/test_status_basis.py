from __future__ import annotations

from scripts.model_manager.ui.controller.busy_service_scope import project_service_busy

from universal_protocol.status_basis import BASIS_KEYS, status_basis


def test_status_basis_keys_match_busy_service_scope() -> None:
    busy = project_service_busy(
        "gateway",
        {"busy": False, "determination": "idle", "restart_would_defer": False},
        as_of="2026-01-01T00:00:00Z",
        manage_pid=1,
        manage_process_start_time="2026-01-01T00:00:00Z",
    )
    for key in BASIS_KEYS:
        assert key in busy
    envelope = status_basis(
        "status",
        "running",
        as_of="2026-01-01T00:00:00Z",
        source="pipeline_tracker",
        scope="execution:abc",
        epoch={"manage_pid": 1},
    )
    assert set(BASIS_KEYS) <= set(busy.keys())
    assert set(BASIS_KEYS) <= set(envelope.keys())
