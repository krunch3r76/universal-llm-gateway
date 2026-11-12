"""Unit tests for deploy_state_gate.require_deploy_state (assertion 20618 item C)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from .admission import FrontierEndpointError
from .deploy_state_gate import (
    _dual_merge_deploy_identity,
    _identity_passes,
    require_deploy_state,
)


@pytest.fixture
def mock_cortex() -> MagicMock:
    return MagicMock()


def _closeout(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"schema_version": 1, "status": "complete", "summary": "s"}
    base.update(overrides)
    return base


def test_empty_gate_files_fail_closed(mock_cortex: MagicMock) -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_deploy_state(
            request_id="r1",
            source_ref=None,
            closeout=_closeout(),
            cortex=mock_cortex,
        )
    assert exc.value.code == "deploy_state_fail_closed"


def test_empty_gate_files_no_surface_declaration_passes(
    mock_cortex: MagicMock,
) -> None:
    mock_cortex.entity_get.return_value = {"attributes": {}}
    require_deploy_state(
        request_id="r2",
        source_ref="todo:x",
        closeout=_closeout(effects_manifest={"surfaces": {}}),
        cortex=mock_cortex,
    )


def test_empty_gate_files_admin_override_passes(mock_cortex: MagicMock) -> None:
    require_deploy_state(
        request_id="r3",
        source_ref="workspaces://a/b.md",
        closeout=_closeout(),
        cortex=mock_cortex,
        admin_override=True,
    )


def test_plan_source_ref_derives_gate_files(
    mock_cortex: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_cortex.entity_get.return_value = {
        "attributes": {"files_expected": ["docs/plan-only.md"]}
    }
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._matched_producers",
        lambda _gate_files: set(),
    )
    require_deploy_state(
        request_id="r4",
        source_ref="plan:deploy-gate",
        closeout=_closeout(),
        cortex=mock_cortex,
    )
    mock_cortex.entity_get.assert_called_once_with("plan:deploy-gate", intent="full")


def test_unresolvable_source_ref_rejects(mock_cortex: MagicMock) -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_deploy_state(
            request_id="r5",
            source_ref="workspaces://a/b.md",
            closeout=_closeout(),
            cortex=mock_cortex,
        )
    assert exc.value.code == "deploy_state_source_unresolvable"
    assert "recovery:" in exc.value.reason


def test_non_empty_surfaces_fail_closed(mock_cortex: MagicMock) -> None:
    mock_cortex.entity_get.return_value = {"attributes": {}}
    with pytest.raises(FrontierEndpointError) as exc:
        require_deploy_state(
            request_id="r6",
            source_ref="todo:x",
            closeout=_closeout(
                effects_manifest={
                    "surfaces": {
                        "mcp_tool": {
                            "surface": "mcp_tool",
                            "source": "conversation",
                            "entries": [{"op": "cortex", "target": "todo:x"}],
                        }
                    }
                }
            ),
            cortex=mock_cortex,
        )
    assert exc.value.code == "deploy_state_fail_closed"


def test_empty_surfaces_declaration_passes(mock_cortex: MagicMock) -> None:
    mock_cortex.entity_get.return_value = {"attributes": {}}
    require_deploy_state(
        request_id="r7",
        source_ref="todo:x",
        closeout=_closeout(effects_manifest={"surfaces": {}}),
        cortex=mock_cortex,
    )


_SHA = "a28906fc180db15916f61f00eb61a633b7781689"
_SYNCED = "2026-11-11T10:00:00Z"
_OTHER_SYNCED = "2026-11-11T11:00:00Z"


def test_dual_merge_get_and_manage_agree_no_mismatch() -> None:
    payload = {
        "deploy_mode": "source_synced",
        "source_synced_at": _SYNCED,
    }
    merged = _dual_merge_deploy_identity(payload, dict(payload), producer="mcp")
    assert merged["source_synced_at"] == _SYNCED


def test_dual_merge_get_empty_manage_fills_gap() -> None:
    manage = {
        "deploy_mode": "source_synced",
        "source_synced_at": _SYNCED,
    }
    merged = _dual_merge_deploy_identity(manage, {}, producer="mcp")
    assert merged["source_synced_at"] == _SYNCED


def test_dual_merge_disagreement_get_wins_and_emits_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[dict[str, str]] = []

    def _capture(**kwargs: str) -> None:
        emitted.append(dict(kwargs))

    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate.emit_deploy_state_gate_dual_read_mismatch",
        _capture,
    )
    manage = {"source_synced_at": _SYNCED}
    get_h = {"source_synced_at": _OTHER_SYNCED}
    merged = _dual_merge_deploy_identity(
        manage, get_h, producer="mcp", request_id="req-dual-1"
    )
    assert merged["source_synced_at"] == _OTHER_SYNCED
    assert emitted == [
        {
            "producer": "mcp",
            "field": "source_synced_at",
            "get_value": _OTHER_SYNCED,
            "manage_value": _SYNCED,
            "request_id": "req-dual-1",
        }
    ]


def test_identity_passes_on_manage_fields_when_get_empty() -> None:
    from datetime import UTC, datetime

    change_ts = datetime(2026, 11, 11, 9, 0, 0, tzinfo=UTC)
    manage = {
        "deploy_mode": "source_synced",
        "source_synced_at": _SYNCED,
    }
    merged = _dual_merge_deploy_identity(manage, {}, producer="mcp")
    passed, _ = _identity_passes(
        merged,
        source_ref=f"git:{_SHA}",
        change_ts=change_ts,
        expected_tree_hash=None,
    )
    assert passed is True


def test_identity_fails_closed_when_stamp_unreadable_fields_absent() -> None:
    from datetime import UTC, datetime

    change_ts = datetime(2026, 11, 11, 9, 0, 0, tzinfo=UTC)
    merged = _dual_merge_deploy_identity(
        {"status": "running", "deploy_stamp_reason": "stamp_unreadable"},
        {},
        producer="mcp",
    )
    passed, had_mode = _identity_passes(
        merged,
        source_ref=f"git:{_SHA}",
        change_ts=change_ts,
        expected_tree_hash=None,
    )
    assert passed is False
    assert had_mode is True


def test_restart_failed_not_fresh_when_manage_not_healthy(
    mock_cortex: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stamp may show synced; unhealthy manage state still blocks the gate."""
    mock_cortex.entity_get.return_value = {"attributes": {}}
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._call_manage_health",
        lambda _p: {
            "status": "unhealthy",
            "detail": "restart failed",
            "deploy_mode": "source_synced",
            "source_synced_at": _SYNCED,
        },
    )
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._fetch_producer_health_json",
        lambda _p: {},
    )
    with pytest.raises(FrontierEndpointError) as exc:
        require_deploy_state(
            request_id="r-sync-fail",
            source_ref="todo:sync-restart-failed",
            closeout=_closeout(files_modified=["services/mcp-server/x.py"]),
            cortex=mock_cortex,
        )
    assert exc.value.code == "deploy_state_pending"
    assert "mcp: starting" in exc.value.reason or "mcp:" in exc.value.reason


def test_mcp_dual_read_passes_manage_only(
    mock_cortex: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_cortex.entity_get.return_value = {"attributes": {}}
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._call_manage_health",
        lambda _p: {
            "status": "running",
            "deploy_mode": "source_synced",
            "source_synced_at": _SYNCED,
        },
    )
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._fetch_producer_health_json",
        lambda _p: {},
    )
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._change_ts_for_source",
        lambda *_a, **_k: __import__("datetime").datetime(
            2026, 11, 11, 9, 0, 0, tzinfo=__import__("datetime").UTC
        ),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._matched_surfaces",
        lambda _g: set(),
    )
    require_deploy_state(
        request_id="r-manage-only",
        source_ref="todo:activation-pending",
        closeout=_closeout(files_modified=["services/mcp-server/tools/foo.py"]),
        cortex=mock_cortex,
    )


def test_stamp_unreadable_manage_health_blocks_gate(
    mock_cortex: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_cortex.entity_get.return_value = {"attributes": {}}
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._call_manage_health",
        lambda _p: {
            "status": "running",
            "deploy_stamp_reason": "stamp_unreadable",
        },
    )
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._fetch_producer_health_json",
        lambda _p: {},
    )
    with pytest.raises(FrontierEndpointError) as exc:
        require_deploy_state(
            request_id="r-stamp-unreadable",
            source_ref="todo:stamp-unreadable-gate",
            closeout=_closeout(files_modified=["services/mcp-server/tools/foo.py"]),
            cortex=mock_cortex,
        )
    assert exc.value.code == "deploy_state_pending"
    assert "stamp_unreadable" in exc.value.reason


def test_stamp_newer_than_process_fails_even_when_get_has_identity(
    mock_cortex: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_cortex.entity_get.return_value = {"attributes": {}}
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._call_manage_health",
        lambda _p: {
            "status": "running",
            "deploy_stamp_reason": "stamp_newer_than_process",
        },
    )
    monkeypatch.setattr(
        "systems.frontier_consult.deploy_state_gate._fetch_producer_health_json",
        lambda _p: {
            "deploy_mode": "source_synced",
            "source_synced_at": _SYNCED,
        },
    )
    with pytest.raises(FrontierEndpointError) as exc:
        require_deploy_state(
            request_id="r-stamp-newer",
            source_ref="todo:stamp-order",
            closeout=_closeout(files_modified=["services/mcp-server/tools/foo.py"]),
            cortex=mock_cortex,
        )
    assert exc.value.code == "deploy_state_pending"
    assert "stamp_newer_than_process" in exc.value.reason


def test_deploy_state_gate_dual_read_mismatch_event_factory() -> None:
    from .deploy_state_gate_events import DeployStateGateDualReadMismatch

    event = DeployStateGateDualReadMismatch(
        producer="mcp",
        field="source_synced_at",
        get_value="a",
        manage_value="b",
        request_id="rid-1",
    )
    assert event.signal == "deploy_state_gate.dual_read_mismatch"
    assert event.payload == {
        "producer": "mcp",
        "field": "source_synced_at",
        "get_value": "a",
        "manage_value": "b",
        "request_id": "rid-1",
    }
