"""Unit tests for deploy_state_gate.require_deploy_state (assertion 20618 item C)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from .admission import FrontierEndpointError
from .deploy_state_gate import require_deploy_state


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
    mock_cortex.entity_get.assert_called_once_with("plan:deploy-gate")


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
