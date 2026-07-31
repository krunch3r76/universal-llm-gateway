"""Tier-M fence regression — manage.* and wildcard denials (6329 t28/t29 class)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.execute_admission import (
    admit_execute_body,
)
from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.tier_m_manifest import (
    DEFAULT_MANIFEST,
    assert_manifest_policy9,
    ratified_manifest_snapshot,
)


def _execute_body(tool_op: str) -> str:
    return (
        "TYPE: DIRECTIVE\n"
        "contract: execute\n"
        f"tool_op: {tool_op}\n"
        "effects_expected: substrate restart observed\n"
        "density: sparse\n"
    )


def test_manifest_twelve_row_pin_unchanged() -> None:
    assert len(DEFAULT_MANIFEST) == 12
    assert ratified_manifest_snapshot() == (
        ("email.pull", True, "idempotent"),
        ("email.search", True, "idempotent"),
        ("email.send", False, "at-most-once"),
        ("email.move", False, "at-most-once"),
        ("email.delete", False, "at-most-once"),
        ("observability.query", True, "idempotent"),
        ("cortex.search", True, "idempotent"),
        ("cortex.entity_get", True, "idempotent"),
        ("cortex.assert", False, "at-most-once"),
        ("fs.*", False, "at-most-once"),
        ("manage.*", False, "at-most-once"),
        ("pipeline.*", False, "at-most-once"),
    )
    assert_manifest_policy9()


@pytest.mark.parametrize(
    "tool_op",
    [
        "manage.sync_restart",
        "manage.wait_healthy",
    ],
)
def test_t28_t29_manage_ops_refused_at_admission(tool_op: str) -> None:
    admission = admit_execute_body(_execute_body(tool_op))
    assert admission.approved is False
    assert admission.error is not None
    assert admission.error["reason"] == "execute_tool_op_denied"
    assert admission.error["tool_op"] in {tool_op, "manage.*"}


@pytest.mark.asyncio
async def test_t28_manage_sync_restart_never_reaches_invoker() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    job = AutoJob(
        job_id="t28",
        thread_id="6329",
        turn_number=28,
        subject="tier-M manage fence",
        body=_execute_body("manage.sync_restart"),
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="low",
        contract="execute",
    )
    with patch(
        "services.git_integration_worker.cursor_auto.handler.maybe_briefing_for_admit",
        new=AsyncMock(return_value=None),
    ):
        result = await process_job(job, bus=client)
    assert result["terminal_status"] == "status:blocked"
    payload = json.loads(client.reply.await_args.kwargs["body"])
    assert payload["reason"] == "execute_tool_op_denied"
    assert payload["tool_op"] in {"manage.sync_restart", "manage.*"}
