"""Park partial sidecar stop_cause durability across budget park and discard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_closeout.bridge_death_harvest import (
    emit_partial_harvest_on_park,
    park_partial_uri,
    read_sidecar_payload,
)
from services.git_integration_worker.cursor_sdk_closeout_seal import (
    seal_closeout_payload,
)
from services.git_integration_worker.cursor_sdk_run_limits import (
    RUN_BUDGET_CONTROL_KEY,
    classify_stop_from_closeout_row,
)


@pytest.fixture
def cortex_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "cortex"
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.bridge_death_harvest.cortex_files_root",
        lambda: root,
    )
    return root


def test_budget_park_sidecar_carries_stop_cause(cortex_root: Path) -> None:
    """Breaks when budget-exhaust park omits durable stop_cause on the sidecar."""
    dispatch_id = "disp-budget-park"
    thread_id = "24977"
    emit_partial_harvest_on_park(
        dispatch_id,
        thread_id=thread_id,
        intent_id=None,
        method="run-budget",
        tool_call_count=8,
        last_tools=[{"tool_name": "shell", "status": "completed"}],
        stop_cause="budget_exhausted",
    )
    uri = park_partial_uri(thread_id=thread_id, dispatch_id=dispatch_id)
    payload = read_sidecar_payload(uri)
    assert payload is not None
    assert payload["stop_cause"] == "budget_exhausted"
    assert payload["tool_call_count"] == 8


def test_idle_discard_preserves_budget_stop_sidecar(cortex_root: Path) -> None:
    """Breaks when idle_discard overwrites a prior budget park with zeros."""
    dispatch_id = "disp-discard-merge"
    thread_id = "24977"
    emit_partial_harvest_on_park(
        dispatch_id,
        thread_id=thread_id,
        intent_id="boot",
        method="run-budget",
        tool_call_count=8,
        stop_cause="budget_exhausted",
    )
    emit_partial_harvest_on_park(
        dispatch_id,
        thread_id=thread_id,
        intent_id=None,
        method="idle_discard",
        tool_call_count=0,
    )
    uri = park_partial_uri(thread_id=thread_id, dispatch_id=dispatch_id)
    payload = read_sidecar_payload(uri)
    assert payload is not None
    assert payload["stop_cause"] == "budget_exhausted"
    assert payload["tool_call_count"] == 8
    forensics = payload.get("forensics") or {}
    records = forensics.get("discard_records")
    assert isinstance(records, list) and records[-1]["method"] == "idle_discard"


def test_delivered_closeout_stop_cause_seals_budget_exhausted() -> None:
    """Breaks when ImplementCloseout publish refuses budget_exhausted stop_cause."""
    record_json = json.dumps(
        {
            RUN_BUDGET_CONTROL_KEY: {
                "budget_exhausted": True,
                "stop_cause": "budget_exhausted",
            }
        }
    )
    assert (
        classify_stop_from_closeout_row(
            {"status": "finished", "record_json": record_json}
        )
        == "budget_exhausted"
    )
    sealed = seal_closeout_payload(
        {
            "schema_version": 1,
            "public_api_changed": False,
            "stop_cause": "budget_exhausted",
            "run_limits": {
                "tool_call_budget": 8,
                "warn_at": 6,
                "upstream_tool_call_limits": "unknown",
                "native_helper_enforcement": "enforced",
            },
        }
    )
    assert sealed["stop_cause"] == "budget_exhausted"
    assert sealed["run_limits"]["tool_call_budget_scope"]
