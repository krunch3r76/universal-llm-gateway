"""Tests for 5968 ratified five — confer, journal, relay trust, bounded dispatch."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from services.git_integration_worker.cursor_auto.gate_serialize import (
    prefer_dispatch_over_park,
)
from services.git_integration_worker.cursor_auto.relay_trust import (
    RELAY_TRUST_SYNTHESIZED_GATE_ENABLED,
    enforce_synthesized_partial,
    parse_synthesized_ack,
    pending_synthesized_closeout,
)
from services.git_integration_worker.cursor_auto.substrate_feedback import (
    extract_substrate_findings,
)
from services.git_integration_worker.cursor_auto.wire_map import (
    resolve_contract_disposition,
    resolve_desired_model,
    resolve_handoff_contract,
)
from services.git_integration_worker.cursor_auto.work_journal import (
    append_journal_entry,
    journal_path,
)


def test_wire_map_confer_contract():
    assert (
        resolve_desired_model("auto", contract="confer")["resolved_model_id"]
        == "cursor/composer-2.5"
    )
    assert resolve_contract_disposition("confer")["disposition_hint"] == "conferred"
    assert resolve_handoff_contract("confer") == "light-bounded"


def test_prefer_dispatch_over_park_holderless_bounded():
    plan = {
        "action": "nest_park",
        "reason": "nest_park_without_holder",
        "gate": {"active": 1, "queued": 0, "limit": 1},
    }
    out = prefer_dispatch_over_park(plan, work_bounded=True)
    assert out["action"] == "dispatch_now"
    assert out["reason"] == "holderless_bounded_prefer_dispatch"


def test_enforce_synthesized_partial():
    assert enforce_synthesized_partial("complete", closeout_source="section2_synthesized") == "complete"
    assert enforce_synthesized_partial("complete", closeout_source="section2_sidecar") == "complete"


def test_pending_synthesized_closeout_requires_ack(monkeypatch):
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.relay_trust.RELAY_TRUST_SYNTHESIZED_GATE_ENABLED",
        True,
    )
    operator = "web-anthropic"
    turns = [
        {
            "from": "cursor-auto",
            "subject": "status:done — implement",
            "body": (
                "TYPE: CLOSEOUT\n"
                "dispatch_id: auto-abc123\n"
                'meta: {"closeout_source": "section2_synthesized"}\n'
            ),
        },
        {
            "from": "web-anthropic",
            "subject": "TYPE: DIRECTIVE",
            "body": "TYPE: DIRECTIVE\nscope: foo",
        },
    ]
    assert pending_synthesized_closeout(turns, operator_from=operator) == "auto-abc123"
    turns.append(
        {
            "from": operator,
            "subject": "ACK",
            "body": "synthesized_closeout_ack: auto-abc123",
        }
    )
    assert pending_synthesized_closeout(turns, operator_from=operator) is None


def test_pending_synthesized_closeout_newest_wins_descending_transport(monkeypatch):
    """A2: bus returns descending; gate must block on newest unacked closeout."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.relay_trust.RELAY_TRUST_SYNTHESIZED_GATE_ENABLED",
        True,
    )
    operator = "web-anthropic"
    turns = [
        {
            "turn_number": 40,
            "from": "cursor-auto",
            "subject": "status:done — older",
            "body": (
                "TYPE: CLOSEOUT\n"
                "dispatch_id: auto-old\n"
                'meta: {"closeout_source": "section2_synthesized"}\n'
            ),
        },
        {
            "turn_number": 42,
            "from": "cursor-auto",
            "subject": "status:done — newer",
            "body": (
                "TYPE: CLOSEOUT\n"
                "dispatch_id: auto-new\n"
                'meta: {"closeout_source": "section2_synthesized"}\n'
            ),
        },
    ]
    descending = list(reversed(turns))
    assert pending_synthesized_closeout(descending, operator_from=operator) == "auto-new"
    turns.append(
        {
            "turn_number": 43,
            "from": operator,
            "subject": "ACK",
            "body": "synthesized_closeout_ack: auto-new",
        }
    )
    descending_with_ack = list(reversed(turns))
    assert (
        pending_synthesized_closeout(descending_with_ack, operator_from=operator)
        == "auto-old"
    )


def test_pending_synthesized_closeout_rejects_foreign_author_ack(monkeypatch):
    """A3: only the operator seat may clear the gate."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.relay_trust.RELAY_TRUST_SYNTHESIZED_GATE_ENABLED",
        True,
    )
    operator = "web-anthropic"
    turns = [
        {
            "from": "cursor-auto",
            "subject": "status:done — implement",
            "body": (
                "TYPE: CLOSEOUT\n"
                "dispatch_id: auto-abc123\n"
                'meta: {"closeout_source": "section2_synthesized"}\n'
            ),
        },
        {
            "from": "cursor-sdk",
            "subject": "status:done",
            "body": "synthesized_closeout_ack: auto-abc123",
        },
    ]
    assert pending_synthesized_closeout(turns, operator_from=operator) == "auto-abc123"


def test_parse_synthesized_ack_rejects_type_ack_alias():
    assert parse_synthesized_ack("TYPE: ACK = auto-deadbeef") is None


def test_parse_synthesized_ack():
    assert parse_synthesized_ack("synthesized_closeout_ack: auto-deadbeef") == "auto-deadbeef"


def test_extract_substrate_findings():
    text = "Hit audit warnings in libs/foo during implement."
    assert extract_substrate_findings(text) == [text]


def test_extract_substrate_findings_identical_true_yields_empty():
    """Incomplete-check AC: identical:true probe lines are not rot findings."""
    line = json.dumps(
        {
            "path": "services/git_integration_worker/cursor_auto/substrate_feedback.py",
            "identical": True,
        }
    )
    assert extract_substrate_findings(line) == []
    assert extract_substrate_findings(f"{line}\naudit warning elsewhere") == [
        "audit warning elsewhere"
    ]


def test_append_journal_entry(tmp_path):
    ok = append_journal_entry(
        thread_id="5968",
        dispatch_id="auto-test",
        contract="implement",
        terminal_status="status:done",
        disposition="dispatched-and-relayed",
        source_repo=tmp_path,
    )
    assert ok is True
    path = journal_path(source_repo=tmp_path)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["thread_id"] == "5968"
    assert row["dispatch_id"] == "auto-test"


def test_process_job_confer_nested(monkeypatch, tmp_path):
    import asyncio

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    submit = AsyncMock(
        return_value={
            "ok": True,
            "dispatch_id": "auto-confer1",
            "execution_id": "exec-auto-confer1",
        }
    )
    polled = AsyncMock(
        return_value={"ok": True, "terminal": True, "status": "completed"}
    )
    sdk_body = AsyncMock(return_value="Confer prose answer.")
    confer = AsyncMock(return_value={"ok": True, "status_code": 200})
    wake = AsyncMock(return_value={"ok": True})

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.poll_dispatch_terminal_with_liveness",
        polled,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.fetch_sdk_closeout_body",
        sdk_body,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_confer",
        confer,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        wake,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.work_journal.append_journal_entry",
        lambda **kwargs: append_journal_entry(source_repo=tmp_path, **kwargs),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_kw: {"active": 0, "queued": 0, "limit": 1},
    )

    job = AutoJob(
        job_id="j-confer",
        thread_id="5968",
        turn_number=40,
        subject="confer routing",
        body="TYPE: DIRECTIVE\nWhat should we do next?",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="confer",
    )
    result = asyncio.run(process_job(job, bus=bus))
    assert result["ok"] is True
    assert result["phase"] == "nested_confer"
    submit.assert_awaited_once()
    assert submit.await_args.kwargs["handoff_contract"] == "light-bounded"
    confer.assert_awaited_once()


def test_pending_synthesized_closeout_ignores_prose_mention_when_meta_sidecar(
    monkeypatch,
):
    """Queue population uses meta closeout_source, not body substring (5968 t67)."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.relay_trust.RELAY_TRUST_SYNTHESIZED_GATE_ENABLED",
        True,
    )
    operator = "web-anthropic"
    turns = [
        {
            "from": "cursor-auto",
            "subject": "status:done — spec sweep",
            "body": (
                "TYPE: CLOSEOUT\n"
                "dispatch_id: auto-74d600c07d8e\n"
                'meta: {"closeout_source": "section2_sidecar"}\n'
                "\n"
                "## ac_verdict\n"
                "Relay-trust fix: gate must key on section2_synthesized label only.\n"
                "\n"
                "deltas_to_spec: none\n"
            ),
        },
    ]
    assert pending_synthesized_closeout(turns, operator_from=operator) is None


def test_pending_synthesized_closeout_gate_disabled_by_default():
    operator = "web-anthropic"
    turns = [
        {
            "from": "cursor-auto",
            "subject": "status:done — prior",
            "body": (
                "TYPE: CLOSEOUT\n"
                "dispatch_id: auto-pending1\n"
                'meta: {"closeout_source": "section2_synthesized"}\n'
            ),
        },
    ]
    assert RELAY_TRUST_SYNTHESIZED_GATE_ENABLED is False
    assert pending_synthesized_closeout(turns, operator_from=operator) is None


def test_pending_synthesized_closeout_parses_all_ack_lines(monkeypatch):
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.relay_trust.RELAY_TRUST_SYNTHESIZED_GATE_ENABLED",
        True,
    )
    operator = "web-anthropic"
    turns = [
        {
            "from": "cursor-auto",
            "subject": "status:done — older",
            "body": (
                "TYPE: CLOSEOUT\n"
                "dispatch_id: auto-old\n"
                'meta: {"closeout_source": "section2_synthesized"}\n'
            ),
        },
        {
            "from": "cursor-auto",
            "subject": "status:done — newer",
            "body": (
                "TYPE: CLOSEOUT\n"
                "dispatch_id: auto-new\n"
                'meta: {"closeout_source": "section2_synthesized"}\n'
            ),
        },
        {
            "from": operator,
            "subject": "ACK batch",
            "body": (
                "synthesized_closeout_ack: auto-old\n"
                "synthesized_closeout_ack: auto-new\n"
            ),
        },
    ]
    assert pending_synthesized_closeout(turns, operator_from=operator) is None


def test_process_job_blocks_directive_after_synthesized_closeout(monkeypatch):
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.relay_trust.RELAY_TRUST_SYNTHESIZED_GATE_ENABLED",
        True,
    )
    import asyncio

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    turns = [
        {
            "from": "cursor-auto",
            "subject": "status:done — prior",
            "body": (
                "TYPE: CLOSEOUT\n"
                "dispatch_id: auto-pending1\n"
                'meta: {"closeout_source": "section2_synthesized"}\n'
            ),
        }
    ]
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=turns),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_kw: {"active": 0, "queued": 0, "limit": 1},
    )

    job = AutoJob(
        job_id="j-block",
        thread_id="5968",
        turn_number=41,
        subject="follow-on",
        body="TYPE: DIRECTIVE\n## Scope\nlibs/foo\nvision: mechanical — synthesized closeout fixture\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:blocked"
    payload = json.loads(bus.reply.await_args_list[-1].kwargs["body"])
    assert payload["pending_synthesized_closeout"] == "auto-pending1"


def test_process_job_blocks_directive_when_relay_trust_unverifiable(monkeypatch):
    """A1: bus read failure must block, not fail-open."""
    import asyncio

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_kw: {"active": 0, "queued": 0, "limit": 1},
    )

    job = AutoJob(
        job_id="j-unverifiable",
        thread_id="5968",
        turn_number=42,
        subject="follow-on",
        body="TYPE: DIRECTIVE\n## Scope\nlibs/foo\nvision: mechanical — synthesized closeout fixture\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:blocked"
    payload = json.loads(bus.reply.await_args_list[-1].kwargs["body"])
    assert payload["relay_trust_unverifiable"] is True
