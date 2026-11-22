"""Acceptance proofs for registry-record knob_resolution reporting (AC3, AC4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from systems.frontier_consult import cursor_sdk_generate as generate_mod
from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.cursor_sdk_prepared_handle import (
    handle_from_dict,
    handle_to_dict,
)

_ARCH_SKILL_REFS = (
    "- Use the `architecture-invariants` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `ulg-architecture` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `docstring-quality` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)

_DENSIFY_FLOOR_REFS = (
    "- Use the `consult-routing` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `lead-seat-boot` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `handoff-packet-authoring` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)

_PACKET = f"""\
<scope>Goal: x. Selection mode: targeted.</scope>
<invariants>[scope] every changed line traces to task.
{_ARCH_SKILL_REFS}
{_DENSIFY_FLOOR_REFS}</invariants>
<task_guidance>## Acceptance criteria
1. It works.</task_guidance>
<corpus>the artifact</corpus>
<mcp_capabilities>LIFE/CORTEX MCP: ON
CODE/VORTEX MCP: OFF — no workspaces or code-only tools.</mcp_capabilities>
<output_format>Reply on thread.</output_format>
"""

_HANDLE_BASE = {
    "request_id": "r",
    "execution_id": "e",
    "dispatch_id": "d",
    "resolved_model": "cursor/composer-2.5",
    "role": "cursor-sdk",
    "family": "f",
    "platform": "p",
    "to_agent": "cursor-sdk",
    "handoff_contract": "implement",
    "thread_subject": "s",
    "pointer_body": "b",
}


def _bind_isolated_cortex_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolated head-schema cortex DB under tmp_path (cortex_store/conftest pattern)."""
    from cortex_store import db as cortex_db
    from cortex_store._test_db_bootstrap import materialize_head_schema_template

    db_path = tmp_path / "cortex_test.db"
    materialize_head_schema_template(db_path)
    monkeypatch.setenv("CORTEX_DB_PATH", str(db_path))
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", db_path)


def _write_primary_packet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_isolated_cortex_db(monkeypatch, tmp_path)
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    packet_dir = tmp_path / "universal-llm-gateway" / "tmp"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "packet.md").write_text(_PACKET, encoding="utf-8")


def _patch_admit_boundaries(
    monkeypatch: pytest.MonkeyPatch, worker_calls: list
) -> None:
    async def fake_worker(*_args: object, **kwargs: object) -> tuple[bool, dict]:
        worker_calls.append(dict(kwargs))
        return (
            True,
            {"status_code": 200, "dispatch_id": "disp-x", "ticket": {}},
        )

    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker", fake_worker)
    monkeypatch.setattr(
        prepare_mod, "create_handoff_thread", AsyncMock(return_value="thread-1")
    )
    monkeypatch.setattr(
        prepare_mod, "admit_handoff_dispatch", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", AsyncMock())
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_signals.publish_frontier_event",
        lambda event: None,
    )


@pytest.mark.asyncio
async def test_admit_reports_knob_outcomes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker_calls: list[dict] = []
    _write_primary_packet(tmp_path, monkeypatch)
    _patch_admit_boundaries(monkeypatch, worker_calls)

    common = dict(
        role="cursor-sdk",
        model="cursor/composer-2.5",
        subject="s",
        caller_agent="dispatch",
        contract="implement",
        packet_path="tmp/packet.md",
        message_text=None,
    )

    result1 = await generate_mod.dispatch_cursor_sdk_generate(
        request_id="req-knob-report-ac3-a",
        model_knobs={"fast": "true", "bogus_knob": "1"},
        **common,
    )
    assert isinstance(result1["knob_resolution"], dict)
    assert result1["knob_resolution"]["fast"] == {
        "status": "accepted",
        "requested": "true",
        "forwarded": "true",
        "supported": ["false", "true"],
    }
    assert result1["knob_resolution"]["bogus_knob"] == {
        "status": "dropped_unsupported",
        "requested": "1",
        "forwarded": None,
        "supported": [],
    }
    assert worker_calls[-1]["model_knobs"] == {"fast": "true"}

    worker_calls.clear()
    result2 = await generate_mod.dispatch_cursor_sdk_generate(
        request_id="req-knob-report-ac3-b",
        model_knobs=None,
        **common,
    )
    assert result2["knob_resolution"]["fast"] == {
        "status": "card_default",
        "requested": "",
        "forwarded": "true",
        "supported": ["false", "true"],
    }
    assert worker_calls[-1]["model_knobs"] == {"fast": "true"}


def test_handle_checkpoint_round_trips_knob_resolution() -> None:
    outcomes = {
        "fast": {
            "status": "accepted",
            "requested": "true",
            "forwarded": "true",
            "supported": ["false", "true"],
        }
    }
    h = handle_from_dict({**_HANDLE_BASE, "knob_resolution": outcomes})
    assert h.knob_resolution == outcomes
    assert handle_from_dict(handle_to_dict(h)).knob_resolution == outcomes
    assert (
        handle_from_dict(
            {**_HANDLE_BASE, "knob_resolution": ["effort", "fast"]}
        ).knob_resolution
        == {}
    )
