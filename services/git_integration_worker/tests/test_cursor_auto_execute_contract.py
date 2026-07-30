"""Option A ``execute`` contract — manifest admission + in-seat single-op runner."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.email_bridge_relay import (
    EMAIL_BRIDGE_EXECUTE_RELAY_FLAG,
    ExecuteRelayRefusalError,
    email_bridge_execute_relay_enabled,
    probe_email_bridge_status,
    relay_email_pull,
)
from services.git_integration_worker.cursor_auto.execute_admission import (
    admit_execute_body,
    parse_tool_args,
    parse_tool_op_tokens,
)
from services.git_integration_worker.cursor_auto.execute_runner import (
    INVOKER_UNCONFIGURED_REASON,
    clear_tool_op_invoker,
    run_tool_op,
    set_tool_op_invoker,
)
from services.git_integration_worker.cursor_auto.execute_tool_op_invoker import (
    is_wired_tool_op,
    register_production_invoker,
)
from services.git_integration_worker.cursor_auto.handler_execute import (
    run_execute_in_seat,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.tier_m_manifest import (
    allowed_tool_ops,
    lookup,
    split_tool_op,
)

_APPROVED_BODY = """TYPE: DIRECTIVE
contract: execute
tool_op: email.pull
tool_args: {"mode": "folder", "folder": "INBOX", "limit": 3}
effects_expected: raw pull JSON relayed inline
"""


class _Reply:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.body = ""


class _FakeBus:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    async def reply(self, **kwargs: Any) -> _Reply:
        self.posts.append(kwargs)
        return _Reply()


class _FakeQueue:
    def __init__(self) -> None:
        self.done: list[tuple[str, bool]] = []

    def mark_done(self, job_id: str, *, failed: bool = False) -> None:
        self.done.append((job_id, failed))


def _job(body: str, *, contract: str = "execute") -> AutoJob:
    return AutoJob(
        job_id="job-1",
        thread_id="6328",
        turn_number=3,
        subject="tier-M pull",
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="low",
        contract=contract,
        request_id="req-abc",
    )


@pytest.fixture(autouse=True)
def _isolate_invoker():
    clear_tool_op_invoker()
    yield
    clear_tool_op_invoker()


# --- manifest ---------------------------------------------------------------


def test_manifest_allows_read_only_relay_and_denies_effectful_send():
    pull = lookup("email.pull")
    assert pull.allowed is True
    assert pull.idempotence == "idempotent"
    assert pull.authority == "life"
    assert "limit" not in pull.note.lower() or "no limit" in pull.note.lower()
    search = lookup("email.search")
    assert search.authority == "life"
    assert lookup("cortex.search").authority == "code"
    assert lookup("email.send").allowed is False


def test_manifest_wildcard_denies_whole_tool():
    row = lookup("manage.sync_restart")
    assert row is not None
    assert row.allowed is False
    assert row.wildcard is True


def test_manifest_denies_by_default_for_unknown_tool():
    assert lookup("stripe.charge") is None


def test_allowed_tool_ops_excludes_denied_rows():
    allowed = allowed_tool_ops()
    assert "email.pull" in allowed
    assert "email.send" not in allowed
    assert "manage.*" not in allowed


def test_split_tool_op_rejects_wrong_shape():
    assert split_tool_op("email") is None
    assert split_tool_op("a.b.c") is None
    assert split_tool_op("Email.Pull") == ("email", "pull")


# --- admission -------------------------------------------------------------


def test_admits_single_allowlisted_op_with_arguments():
    admission = admit_execute_body(_APPROVED_BODY)
    assert admission.approved is True
    assert admission.row is not None
    assert admission.row.tool_op == "email.pull"
    assert admission.arguments["folder"] == "INBOX"
    assert admission.error is None


def test_missing_tool_op_blocks_with_fix_hint_and_allowed_set():
    admission = admit_execute_body("TYPE: DIRECTIVE\ncontract: execute\n")
    assert admission.approved is False
    assert admission.error["reason"] == "execute_tool_op_missing"
    assert admission.error["fix_hint"]
    assert "email.pull" in admission.error["allowed_tool_ops"]


def test_multi_op_is_refused_as_judgment_not_execution():
    body = "TYPE: DIRECTIVE\ntool_op: email.pull\ntool_op: cortex.search\n"
    admission = admit_execute_body(body)
    assert admission.error["reason"] == "execute_multi_op_unsupported"
    assert admission.error["declared_tool_ops"] == ["email.pull", "cortex.search"]


def test_denied_op_blocks_and_names_the_manifest_reason():
    admission = admit_execute_body("TYPE: DIRECTIVE\ntool_op: email.send\n")
    assert admission.error["reason"] == "execute_tool_op_denied"
    assert admission.error["tool_op"] == "email.send"
    assert admission.error["manifest_note"]


def test_unknown_op_blocks_deny_by_default():
    admission = admit_execute_body("TYPE: DIRECTIVE\ntool_op: stripe.charge\n")
    assert admission.error["reason"] == "execute_tool_op_not_in_manifest"


def test_unparseable_tool_args_blocks_rather_than_defaulting_to_empty():
    body = (
        "TYPE: DIRECTIVE\ntool_op: email.pull\n"
        "effects_expected: raw JSON\ntool_args: folder=INBOX\n"
    )
    admission = admit_execute_body(body)
    assert admission.error["reason"] == "execute_tool_args_unparseable"
    assert admission.error["provided"] == "folder=INBOX"


def test_missing_effects_expected_blocks_an_otherwise_valid_op():
    admission = admit_execute_body("TYPE: DIRECTIVE\ntool_op: email.pull\n")
    assert admission.error["reason"] == "execute_effects_expected_missing"
    assert admission.error["tool_op"] == "email.pull"


def test_denial_precedes_the_effects_expected_gate():
    admission = admit_execute_body("TYPE: DIRECTIVE\ntool_op: email.send\n")
    assert admission.error["reason"] == "execute_tool_op_denied"


def test_absent_tool_args_is_an_empty_map():
    arguments, bad = parse_tool_args("TYPE: DIRECTIVE\ntool_op: email.pull\n")
    assert arguments == {}
    assert bad is None


def test_tool_op_tokens_preserve_authored_order():
    body = "tool_op: cortex.search\ntool_op: email.pull\n"
    assert parse_tool_op_tokens(body) == ("cortex.search", "email.pull")


# --- runner ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tool_op_without_invoker_refuses_instead_of_pretending():
    outcome = await run_tool_op(lookup("email.pull"), {})
    assert outcome.ok is False
    assert outcome.reason == INVOKER_UNCONFIGURED_REASON


@pytest.mark.asyncio
async def test_run_tool_op_returns_observed_payload():
    async def _invoker(*, tool: str, op: str, arguments: dict[str, Any]):
        return {"tool": tool, "op": op, "count": arguments.get("limit")}

    set_tool_op_invoker(_invoker)
    outcome = await run_tool_op(lookup("email.pull"), {"limit": 3})
    assert outcome.ok is True
    assert outcome.payload == {"tool": "email", "op": "pull", "count": 3}


@pytest.mark.asyncio
async def test_run_tool_op_captures_invoker_exception():
    async def _invoker(**_: Any):
        raise RuntimeError("imap down")

    set_tool_op_invoker(_invoker)
    outcome = await run_tool_op(lookup("email.pull"), {})
    assert outcome.ok is False
    assert outcome.reason == "execute_invoker_raised"
    assert "imap down" in outcome.error


@pytest.mark.asyncio
async def test_non_object_payload_is_refused():
    async def _invoker(**_: Any):
        return ["not", "an", "object"]

    set_tool_op_invoker(_invoker)
    outcome = await run_tool_op(lookup("email.pull"), {})
    assert outcome.reason == "execute_payload_not_object"


# --- in-seat terminal ------------------------------------------------------


@pytest.mark.asyncio
async def test_in_seat_success_relays_raw_payload_inline():
    async def _invoker(**_: Any):
        return {"messages": [{"subject": "hello"}]}

    set_tool_op_invoker(_invoker)
    bus, queue = _FakeBus(), _FakeQueue()
    result = await run_execute_in_seat(
        _job(_APPROVED_BODY),
        client=bus,
        queue=queue,
        model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
        effort={"requested": "low", "resolved_effort": "low"},
        gate_plan={"action": "in_seat"},
    )
    assert result["terminal_status"] == "status:done"
    assert result["disposition"] == "executed"
    posted = bus.posts[0]
    assert posted["subject"].startswith("status:done")
    assert "hello" in posted["body"]
    assert "req-abc" in posted["body"]
    assert queue.done == [("job-1", False)]


@pytest.mark.asyncio
async def test_in_seat_without_invoker_is_needs_attended_not_done():
    bus, queue = _FakeBus(), _FakeQueue()
    result = await run_execute_in_seat(
        _job(_APPROVED_BODY),
        client=bus,
        queue=queue,
        model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
        effort={"requested": "low", "resolved_effort": "low"},
        gate_plan={"action": "in_seat"},
    )
    assert result["terminal_status"] == "status:needs-attended"
    assert INVOKER_UNCONFIGURED_REASON in bus.posts[0]["body"]


@pytest.mark.asyncio
async def test_in_seat_refuses_when_admission_was_bypassed():
    bus, queue = _FakeBus(), _FakeQueue()
    result = await run_execute_in_seat(
        _job("TYPE: DIRECTIVE\ncontract: execute\ntool_op: email.send\n"),
        client=bus,
        queue=queue,
        model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
        effort={"requested": "low", "resolved_effort": "low"},
        gate_plan={"action": "in_seat"},
    )
    assert result["terminal_status"] == "status:blocked"
    assert "execute_tool_op_denied" in bus.posts[0]["body"]


# --- email-bridge relay (life authority) -----------------------------------


def test_email_bridge_relay_flag_defaults_off(monkeypatch):
    monkeypatch.delenv(EMAIL_BRIDGE_EXECUTE_RELAY_FLAG, raising=False)
    assert email_bridge_execute_relay_enabled() is False
    assert is_wired_tool_op("email", "pull") is False
    assert is_wired_tool_op("email", "search") is False
    assert is_wired_tool_op("cortex", "search") is True


def test_email_ops_wired_only_when_flag_enabled(monkeypatch):
    monkeypatch.setenv(EMAIL_BRIDGE_EXECUTE_RELAY_FLAG, "1")
    assert email_bridge_execute_relay_enabled() is True
    assert is_wired_tool_op("email", "pull") is True
    assert is_wired_tool_op("email", "send") is False


def test_relay_pull_rejects_unknown_keys():
    with pytest.raises(ExecuteRelayRefusalError) as exc:
        relay_email_pull({"mode": "folder", "folder": "INBOX", "limit": 3})
    assert exc.value.reason == "execute_args_schema_invalid"


def test_relay_pull_timeout_budget_at_least_120_seconds():
    from services.git_integration_worker.cursor_auto import email_bridge_relay as mod

    assert mod._PULL_TIMEOUT >= 120.0


def test_mailbox_guard_blocks_wrong_surface_before_imap_call():
    with (
        patch(
            "services.git_integration_worker.cursor_auto.email_bridge_relay.check_mailbox_surface",
            return_value={
                "error": "wrong_surface",
                "message": "use Graph export",
            },
        ),
        patch(
            "services.git_integration_worker.cursor_auto.email_bridge_relay._request",
        ) as mock_request,
    ):
        with pytest.raises(ExecuteRelayRefusalError) as exc:
            relay_email_pull({"mode": "folder", "mailbox": "user@corp.com"})
        assert exc.value.reason == "execute_relay_mailbox_guard"
        mock_request.assert_not_called()


def test_degraded_empty_search_marks_indeterminate():
    from services.git_integration_worker.cursor_auto.email_bridge_relay import (
        relay_email_search,
    )

    with (
        patch(
            "services.git_integration_worker.cursor_auto.email_bridge_relay._fetch_bridge_status",
            return_value={"healthy": False, "degraded_reason": "imap lag"},
        ),
        patch(
            "services.git_integration_worker.cursor_auto.email_bridge_relay._request",
            return_value={"total": 0, "emails": []},
        ),
    ):
        result = relay_email_search({"mailbox": "INBOX"})
    assert result.get("status") == "indeterminate"


def test_probe_email_bridge_status_records_unreachable():
    with patch(
        "services.git_integration_worker.cursor_auto.email_bridge_relay._request",
        side_effect=ExecuteRelayRefusalError("execute_relay_unreachable", "down"),
    ):
        result = probe_email_bridge_status()
    assert result["reachable"] is False
    assert result["reason"] == "execute_relay_unreachable"


@pytest.mark.asyncio
async def test_run_tool_op_maps_relay_refusal_to_named_reason():
    async def _invoker(**_: Any):
        raise ExecuteRelayRefusalError("execute_relay_unreachable", "bridge down")

    set_tool_op_invoker(_invoker)
    outcome = await run_tool_op(lookup("email.pull"), {"mode": "folder"})
    assert outcome.ok is False
    assert outcome.reason == "execute_relay_unreachable"
    assert "bridge down" in (outcome.error or "")


@pytest.mark.asyncio
async def test_production_invoker_email_pull_with_flag_and_mock_relay(monkeypatch):
    monkeypatch.setenv(EMAIL_BRIDGE_EXECUTE_RELAY_FLAG, "true")
    register_production_invoker()
    pull_payload = {"emails": [{"subject": "hi"}], "total": 1}
    with patch(
        "services.git_integration_worker.cursor_auto.execute_tool_op_invoker.relay_email_pull",
        return_value=pull_payload,
    ):
        bus, queue = _FakeBus(), _FakeQueue()
        body = """TYPE: DIRECTIVE
contract: execute
tool_op: email.pull
tool_args: {"mode": "folder", "folder": "INBOX"}
effects_expected: raw pull JSON relayed inline
"""
        result = await run_execute_in_seat(
            _job(body),
            client=bus,
            queue=queue,
            model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
            effort={"requested": "low", "resolved_effort": "low"},
            gate_plan={"action": "in_seat"},
        )
    assert result["terminal_status"] == "status:done"
    assert "hi" in bus.posts[0]["body"]

