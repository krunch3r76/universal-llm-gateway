"""S1 — _audit_gates WARN→advisory (todo:session-close-light-latency)."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops._session_close_validate import _audit_gates
from cortex_store.dispatch_ops.adapters._doc_validate import _op_doc_validate


@pytest.mark.offline
def test_audit_gates_empty_audit_passes() -> None:
    gates = _audit_gates({})
    assert gates == [{"gate": "audit", "status": "passed"}]


@pytest.mark.offline
def test_audit_gates_blocked_fails() -> None:
    gates = _audit_gates(
        {
            "blocked": True,
            "error": "session_audit blocked close — critical gaps unresolved",
            "criticals": [{"kind": "x", "severity": "critical"}],
        }
    )
    assert len(gates) == 1
    assert gates[0]["gate"] == "audit_blocked"
    assert gates[0]["status"] == "failed"


@pytest.mark.offline
def test_audit_gates_warn_deferred_passed_advisory() -> None:
    gates = _audit_gates(
        {
            "warning": {
                "mode": "warn",
                "gap_count": 1,
                "deferred": ["confirmed_attribute_no_assertion"],
                "by_kind": {"confirmed_attribute_no_assertion": 1},
                "findings_sample": [
                    {
                        "kind": "confirmed_attribute_no_assertion",
                        "severity": "warning",
                        "subject": "service:mcp-server",
                    }
                ],
            }
        }
    )
    assert len(gates) == 1
    g = gates[0]
    assert g["gate"] == "audit.confirmed_attribute_no_assertion"
    assert g["status"] == "passed"
    assert g["advisory"] is True
    assert g["deferred"] is True
    assert g["severity"] == "warning"
    assert g["count"] == 1


@pytest.mark.offline
def test_audit_gates_warn_non_deferred_still_non_blocking() -> None:
    gates = _audit_gates(
        {
            "warning": {
                "mode": "warn",
                "gap_count": 1,
                "deferred": [],
                "by_kind": {"confirmed_attribute_no_assertion": 2},
                "audit_findings": [
                    {
                        "kind": "confirmed_attribute_no_assertion",
                        "severity": "warning",
                        "subject": "service:mcp-server",
                    }
                ],
            }
        }
    )
    assert gates[0]["status"] == "passed"
    assert gates[0]["advisory"] is True
    assert "deferred" not in gates[0]


@pytest.mark.offline
def test_doc_validate_warn_findings_pass_with_advisory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_preflight(**kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "session_id": "cursor-2026-06-30-1200-abc",
            "audit": {
                "warning": {
                    "mode": "warn",
                    "gap_count": 1,
                    "deferred": ["confirmed_attribute_no_assertion"],
                    "by_kind": {"confirmed_attribute_no_assertion": 1},
                    "findings_sample": [
                        {
                            "kind": "confirmed_attribute_no_assertion",
                            "severity": "warning",
                            "subject": "service:mcp-server",
                        }
                    ],
                }
            },
            "warnings": [],
            "turn_count": 0,
        }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_session_close._op_session_close_preflight",
        _fake_preflight,
    )
    result = _op_doc_validate(
        doc_type="session_close",
        session_id="cursor-2026-06-30-1200-abc",
        agent="cursor",
        session_summary_md="## Session Summary\n\nDone.",
        summary="Arc: closed the loop on session close advisory gates.",
        transcript_depth="light",
        entity_ids=["service:mcp-server"],
        defer_gaps={"confirmed_attribute_no_assertion": "pre-existing"},
    )
    assert result["status"] == "pass"
    audit_gates = [g for g in result["gates"] if str(g.get("gate", "")).startswith("audit.")]
    assert audit_gates
    assert all(g["status"] == "passed" for g in audit_gates)
    assert all(g.get("advisory") is True for g in audit_gates)


@pytest.mark.offline
def test_doc_validate_blocked_audit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_preflight(**kwargs: object) -> dict[str, object]:
        return {
            "ok": False,
            "reason": "session_audit_blocked",
            "blocked": True,
            "error": "session_audit blocked close — critical gaps unresolved",
            "criticals": [{"kind": "x", "severity": "critical"}],
        }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_session_close._op_session_close_preflight",
        _fake_preflight,
    )
    result = _op_doc_validate(
        doc_type="session_close",
        session_id="cursor-2026-06-30-1200-abc",
        agent="cursor",
        session_summary_md="## Session Summary\n\nDone.",
        summary="Arc: blocked audit must still fail validation.",
        transcript_depth="light",
    )
    assert result["status"] != "pass"
