"""Harvest returned-footer gate — fail-closed §B row 8."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    emit_footer,
)
from scripts.model_manager.ui.controller.charter_runner.harvest import (
    harvest_completed_windows,
    turn_number,
)
from scripts.model_manager.ui.controller.charter_runner.harvest_footer_gate import (
    footer_field_path,
    reject_harvest_without_footer,
)

pytestmark = pytest.mark.offline

_VALID_FOOTER = emit_footer(
    schema_version=1,
    status="CHECKPOINT",
    next_pickup={"gid": "G1", "lane": "judgment", "executor": "cursor-sdk"},
    wip=None,
    consult={"role": None, "poll_hint": None, "from": None},
    revise_count=0,
    evidence=[],
    window_id="charter-6006-w1",
    transition_id=None,
)

_CHECKPOINT_BODY = f"""# CHECKPOINT

## Next pickup
1. G1 — step

## Frictions
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline → CHECKPOINT.

{_VALID_FOOTER}
"""


def test_footerless_checkpoint_rejected_at_gate() -> None:
    assert reject_harvest_without_footer(
        root_id="6006",
        window_index=1,
        checkpoint_subject="CHECKPOINT — window 1",
        checkpoint_body="no footer",
    )


def test_malformed_footer_names_field_path() -> None:
    ok, field_path = footer_field_path("```charter-state\n{}\n```")
    assert ok is False
    assert field_path


def test_valid_footer_passes_gate() -> None:
    assert not reject_harvest_without_footer(
        root_id="6006",
        window_index=1,
        checkpoint_subject="CHECKPOINT — window 1",
        checkpoint_body=_CHECKPOINT_BODY,
    )


def test_invalid_next_pickup_gid_rejected() -> None:
    bad = emit_footer(
        schema_version=1,
        status="CHECKPOINT",
        next_pickup={"gid": "", "lane": "judgment", "executor": "x"},
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id="charter-6006-w1",
        transition_id=None,
    )
    assert reject_harvest_without_footer(
        root_id="6006",
        window_index=1,
        checkpoint_subject="CHECKPOINT — window 1",
        checkpoint_body=f"prose\n\n{bad}",
    )


@pytest.mark.asyncio
async def test_harvest_skips_footerless_checkpoint(tmp_path, monkeypatch) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log

    monkeypatch.setattr(window_log, "_HARVESTED_DIR", tmp_path)

    admission = {
        "turn_number": 10,
        "subject": "WIP charter-runner window 1",
        "body": (
            '{"charter_runner":true,"window":1,"posted_at":"2026-07-27T00:00:00Z",'
            '"worker_thread":"worker-1","admission_mode":"generate"}'
        ),
    }
    checkpoint = {
        "turn_number": 20,
        "subject": "CHECKPOINT — window 1 complete",
        "body": "footerless CHECKPOINT body",
    }
    turns = [admission, checkpoint]

    with patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.bus_client.fetch_thread",
        new=AsyncMock(return_value={"slug": "arc", "summary": "so what"}),
    ), patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.bus_client.fetch_turns",
        new=AsyncMock(return_value=[]),
    ), patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.bus_client.close_worker_thread",
        new=AsyncMock(),
    ), patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.after_window_terminal_harvested",
        new=AsyncMock(),
    ), patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.window_log.append_closeout",
    ) as append_closeout:
        attrs = await harvest_completed_windows("6006", turns)

    assert attrs == []
    append_closeout.assert_not_called()
    assert not window_log.already_harvested("6006", 1)


@pytest.mark.asyncio
async def test_harvest_accepts_valid_footer(tmp_path, monkeypatch) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log

    monkeypatch.setattr(window_log, "_HARVESTED_DIR", tmp_path)

    admission = {
        "turn_number": 10,
        "subject": "WIP charter-runner window 1",
        "body": (
            '{"charter_runner":true,"window":1,"posted_at":"2026-07-27T00:00:00Z",'
            '"worker_thread":"worker-1","admission_mode":"generate"}'
        ),
    }
    checkpoint = {
        "turn_number": 20,
        "subject": "CHECKPOINT — window 1 complete",
        "body": _CHECKPOINT_BODY,
    }
    turns = [admission, checkpoint]

    with patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.bus_client.fetch_thread",
        new=AsyncMock(return_value={"slug": "arc", "summary": "so what"}),
    ), patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.bus_client.fetch_turns",
        new=AsyncMock(return_value=[]),
    ), patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.bus_client.close_worker_thread",
        new=AsyncMock(),
    ), patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.after_window_terminal_harvested",
        new=AsyncMock(),
    ), patch(
        "scripts.model_manager.ui.controller.charter_runner.harvest.events.emit_manage_charter_tick_closed",
        new=AsyncMock(),
    ):
        await harvest_completed_windows("6006", turns)

    assert window_log.already_harvested("6006", 1)


def test_machine_self_heal_not_rejected_by_gate() -> None:
    assert not reject_harvest_without_footer(
        root_id="6006",
        window_index=1,
        checkpoint_subject="CHECKPOINT — self-heal checkpoint_missing",
        checkpoint_body="no charter-state fence",
    )
