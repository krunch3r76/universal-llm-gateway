"""Production invoker wiring — manifest ratification + per-op-class e2e."""

import json
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.execute_runner import (
    INVOKER_UNCONFIGURED_REASON,
    clear_tool_op_invoker,
)
from services.git_integration_worker.cursor_auto.execute_tool_op_invoker import (
    is_wired_tool_op,
    production_tool_op_invoker,
    register_production_invoker,
)
from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.handler_execute import (
    run_execute_in_seat,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.tier_m_manifest import (
    PENDING_OPERATOR_BIND,
    allowed_tool_ops,
    ratified_manifest_snapshot,
)

_RATIFIED_ROWS: tuple[tuple[str, bool, str], ...] = (
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


def test_manifest_ratified_rows_match_spec_section3_and_7():
    assert PENDING_OPERATOR_BIND is False
    assert ratified_manifest_snapshot() == _RATIFIED_ROWS
    assert set(allowed_tool_ops()) == {
        "email.pull",
        "email.search",
        "observability.query",
        "cortex.search",
        "cortex.entity_get",
    }


def test_wildcard_rows_are_deny_only():
    for tool_op, allowed, _ in ratified_manifest_snapshot():
        if tool_op.endswith(".*"):
            assert allowed is False


def test_email_ops_are_not_wired_on_code_surface():
    assert is_wired_tool_op("email", "pull") is False
    assert is_wired_tool_op("cortex", "search") is True


class _Reply:
    status_code = 200
    body = ""


class _FakeBus:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    async def reply(self, **kwargs: Any) -> _Reply:
        self.posts.append(kwargs)
        return _Reply()


class _FakeQueue:
    def __init__(self) -> None:
        self.done: list[tuple[str, bool]] = []

    def mark_done(
        self,
        job_id: str,
        *,
        failed: bool = False,
        terminal_reason: str | None = None,
    ) -> None:
        self.done.append((job_id, failed))


def _execute_job(tool_op: str, tool_args: dict[str, Any] | None = None) -> AutoJob:
    lines = [
        "TYPE: DIRECTIVE",
        "contract: execute",
        f"tool_op: {tool_op}",
        "effects_expected: raw tool payload inline",
    ]
    if tool_args is not None:
        lines.append(f"tool_args: {json.dumps(tool_args)}")
    return AutoJob(
        job_id="job-inv",
        thread_id="6328",
        turn_number=9,
        subject="tier-M execute",
        body="\n".join(lines) + "\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="low",
        contract="execute",
        request_id="req-g3p2-invoker",
    )


@pytest.fixture(autouse=True)
def _isolate_invoker():
    clear_tool_op_invoker()
    yield
    clear_tool_op_invoker()


@pytest.mark.asyncio
async def test_email_pull_admitted_but_unwired_refuses_unconfigured():
    register_production_invoker()
    bus, queue = _FakeBus(), _FakeQueue()
    result = await run_execute_in_seat(
        _execute_job("email.pull", {"mode": "folder", "folder": "INBOX", "limit": 1}),
        client=bus,
        queue=queue,
        model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
        effort={"requested": "low", "resolved_effort": "low"},
        gate_plan={"action": "in_seat"},
    )
    assert result["terminal_status"] == "status:needs-attended"
    assert INVOKER_UNCONFIGURED_REASON in bus.posts[0]["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_op", "tool_args", "relay_fn", "relay_return"),
    [
        (
            "cortex.search",
            {"query": "tier-m", "limit": 2},
            "_relay_cortex_dispatch",
            {"query": "tier-m", "items": [{"id": 1}], "total": 1},
        ),
        (
            "cortex.entity_get",
            {"entity_id": "todo:test"},
            "_relay_cortex_dispatch",
            {"entity_id": "todo:test", "name": "Test"},
        ),
        (
            "observability.query",
            {"operation": "operations"},
            "_relay_observability_query",
            {"operations": ["recent-failures"]},
        ),
    ],
)
async def test_wired_op_e2e_relays_observed_payload(
    tool_op: str,
    tool_args: dict[str, Any],
    relay_fn: str,
    relay_return: dict[str, Any],
) -> None:
    register_production_invoker()
    bus, queue = _FakeBus(), _FakeQueue()
    with patch(
        f"services.git_integration_worker.cursor_auto.execute_tool_op_invoker.{relay_fn}",
        return_value=relay_return,
    ):
        result = await run_execute_in_seat(
            _execute_job(tool_op, tool_args),
            client=bus,
            queue=queue,
            model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
            effort={"requested": "low", "resolved_effort": "low"},
            gate_plan={"action": "in_seat"},
        )
    assert result["terminal_status"] == "status:done"
    assert result["disposition"] == "executed"
    body = bus.posts[0]["body"]
    assert "req-g3p2-invoker" in body
    payload = json.loads(body)
    assert payload["tool_payload"] == relay_return


@pytest.mark.asyncio
async def test_unallowlisted_op_blocked_at_admission():
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    body = (
        "TYPE: DIRECTIVE\ncontract: execute\n"
        "tool_op: stripe.charge\neffects_expected: x\n"
    )
    with patch(
        "services.git_integration_worker.cursor_auto.handler.maybe_briefing_for_admit",
        new=AsyncMock(return_value=None),
    ):
        result = await process_job(
            replace(_execute_job("stripe.charge"), body=body),
            bus=client,
        )
    assert result["terminal_status"] == "status:blocked"


@pytest.mark.asyncio
async def test_production_invoker_callable_is_async():
    register_production_invoker()
    with patch(
        "services.git_integration_worker.cursor_auto.execute_tool_op_invoker._relay_cortex_dispatch",
        return_value={"items": []},
    ):
        payload = await production_tool_op_invoker(
            tool="cortex",
            op="search",
            arguments={"query": "x"},
        )
    assert payload == {"items": []}
