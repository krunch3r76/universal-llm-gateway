"""Tests for ``contract: propagate`` operator restart requests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.directive import has_actionable_scope
from services.git_integration_worker.cursor_auto.propagate_admission import (
    admit_propagate_body,
)

_MCP_YAML_BODY = """\
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart
effects_expected: propagation row persisted; restart executed or deferred

## propagation
```yaml
propagation:
  - service: mcp
    code_ref: deadbeef
    proof_class: client_visible
```
"""

_SHORTHAND_BODY = """\
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart mcp
code_ref: cafebabe
effects_expected: propagation row persisted; restart executed or deferred
"""


def test_propagation_scope_is_actionable() -> None:
    assert has_actionable_scope("scope: propagation sync_restart mcp")
    assert has_actionable_scope("## propagation\n```yaml\npropagation: []\n```")


def test_admit_yaml_propagation_rows() -> None:
    admission = admit_propagate_body(_MCP_YAML_BODY)
    assert admission.approved
    assert len(admission.rows) == 1
    assert admission.rows[0].service == "mcp"
    assert admission.rows[0].code_ref == "deadbeef"


def test_admit_shorthand_propagation() -> None:
    admission = admit_propagate_body(_SHORTHAND_BODY)
    assert admission.approved
    assert admission.rows[0].service == "mcp"
    assert admission.rows[0].code_ref == "cafebabe"


def test_admit_rejects_missing_effects_expected() -> None:
    body = _SHORTHAND_BODY.replace("effects_expected:", "expected:")
    admission = admit_propagate_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "propagate_effects_expected_missing"


def test_admit_rejects_missing_rows() -> None:
    body = """\
TYPE: DIRECTIVE
contract: propagate
effects_expected: nothing to do
"""
    admission = admit_propagate_body(body)
    assert not admission.approved
    assert admission.error["reason"] == "propagate_rows_missing"


@pytest.mark.asyncio
async def test_run_propagation_queues_when_manage_defers() -> None:
    from services.git_integration_worker.cursor_auto.handler_propagation import (
        run_propagation_in_seat,
    )
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    job = AutoJob(
        job_id="job-1",
        thread_id=6339,
        turn_number=1,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="restart git_integration_worker",
        body=_SHORTHAND_BODY.replace("mcp", "git_integration_worker"),
        contract="propagate",
        desired_model="auto",
        desired_effort="medium",
        require_attended=False,
        request_id="req-1",
    )
    calls: list[str] = []

    class _Queue:
        def mark_done(self, job_id: str, *, failed: bool = False) -> None:
            calls.append(job_id)

    class _Client:
        async def reply(self, **kwargs):  # type: ignore[no-untyped-def]
            return type("R", (), {"status_code": 200, "body": ""})()

    with (
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.upsert_open_rows",
            return_value=["git_integration_worker:cafebabe:sync_restart"],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.sync_restart_service",
            return_value={
                "status": "deferred",
                "state": "draining",
                "restart_intent_id": "intent-giw-1",
                "reason": "draining; completion delivered via git_worker.drain events",
            },
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
        ),
    ):
        result = await run_propagation_in_seat(
            job,
            client=_Client(),
            queue=_Queue(),
            model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
            effort={"requested": None, "resolved_effort": "medium"},
            gate_plan={"action": "in_seat"},
        )
    assert result["disposition"] == "queued"
    summary = str(result.get("summary") or "")
    assert "queued" in summary.lower()
    assert "manage drain" in summary.lower()
    assert calls == ["job-1"]


@pytest.mark.asyncio
async def test_run_propagation_blocked_when_manage_defers_without_intent() -> None:
    from services.git_integration_worker.cursor_auto.handler_propagation import (
        run_propagation_in_seat,
    )
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    job = AutoJob(
        job_id="job-mcp-busy",
        thread_id=6339,
        turn_number=2,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="restart mcp",
        body=_SHORTHAND_BODY,
        contract="propagate",
        desired_model="auto",
        desired_effort="medium",
        require_attended=False,
        request_id="req-mcp-busy",
    )

    class _Queue:
        def mark_done(self, job_id: str, *, failed: bool = False) -> None:
            pass

    class _Client:
        async def reply(self, **kwargs):  # type: ignore[no-untyped-def]
            return type("R", (), {"status_code": 200, "body": ""})()

    with (
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.upsert_open_rows",
            return_value=["mcp:cafebabe:sync_restart"],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.sync_restart_service",
            return_value={
                "status": "deferred",
                "state": "busy",
                "reason": "cdp_ask_live",
                "retry_after_s": 30,
            },
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
        ),
    ):
        result = await run_propagation_in_seat(
            job,
            client=_Client(),
            queue=_Queue(),
            model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
            effort={"requested": None, "resolved_effort": "medium"},
            gate_plan={"action": "in_seat"},
        )
    assert result["disposition"] == "blocked"
    summary = str(result.get("summary") or "")
    assert "nothing will fire" in summary.lower()
    assert "will fire after drain" not in summary.lower()
