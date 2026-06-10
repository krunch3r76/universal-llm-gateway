"""Unit tests for the session-close audit GATE scope contract (thread 1448).

The gate (`_run_session_audit_or_block`) audits only the entities a session
declares in ``entity_ids``. With no ``entity_ids`` it MUST skip the scan and
return {} (emitting a ``cortex.session.audit.unscoped`` observation), rather
than falling through to a full-graph scan. The full-graph behavior is reserved
for the user-callable advisory ``session_audit`` op and the ``audit`` tool.

These tests stub the scan + event sink, so no DB is required.
"""

from __future__ import annotations

import cortex_store.dispatch_ops.ops_review_gate as gate
from cortex_store.dispatch_ops.ops_review_gate import _run_session_audit_or_block


def test_empty_entity_ids_skips_scan_and_returns_clean(monkeypatch):
    """No entity_ids -> gate returns {} without invoking the graph scan."""
    scan_calls: list[tuple] = []
    events: list[tuple[str, dict]] = []

    def _spy_scan(session_id, entity_ids):
        scan_calls.append((session_id, tuple(entity_ids)))
        raise AssertionError("scan must not run when entity_ids is empty")

    monkeypatch.setattr(gate, "_run_session_audit_graph_only", _spy_scan)
    monkeypatch.setattr(gate, "record", lambda signal, **kw: events.append((signal, kw)))
    monkeypatch.setenv("CORTEX_SESSION_AUDIT_MODE", "warn")

    out = _run_session_audit_or_block(
        session_id="claude-web-test-empty",
        agent="claude-web",
        entity_ids=[],
        defer_gaps=None,
    )

    assert out == {}
    assert scan_calls == []
    signals = [s for s, _ in events]
    assert signals == ["cortex.session.audit.unscoped"]
    assert events[0][1]["reason"] == "no_entity_ids"


def test_block_mode_empty_entity_ids_does_not_block(monkeypatch):
    """BLOCK mode + no entity_ids -> still a clean {}; never blocks on graph-wide debt."""
    monkeypatch.setattr(
        gate,
        "_run_session_audit_graph_only",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("scan must not run when entity_ids is empty")
        ),
    )
    monkeypatch.setattr(gate, "record", lambda signal, **kw: None)
    monkeypatch.setenv("CORTEX_SESSION_AUDIT_MODE", "block")

    out = _run_session_audit_or_block(
        session_id="claude-web-test-empty-block",
        agent="claude-web",
        entity_ids=[],
        defer_gaps=None,
    )

    assert out == {}
    assert "blocked" not in out


def test_nonempty_entity_ids_still_scans_scoped(monkeypatch):
    """entity_ids present -> scoped scan runs; a warning finding flows through; no unscoped event."""
    scan_calls: list[tuple] = []
    events: list[str] = []
    finding = {
        "kind": "entity_empty_description",
        "subject": "service:x",
        "severity": "warning",
        "detail": "empty",
        "audit_id": "a1",
    }

    def _spy_scan(session_id, entity_ids):
        scan_calls.append((session_id, tuple(entity_ids)))
        return [finding]

    monkeypatch.setattr(gate, "_run_session_audit_graph_only", _spy_scan)
    monkeypatch.setattr(gate, "record", lambda signal, **kw: events.append(signal))
    monkeypatch.setenv("CORTEX_SESSION_AUDIT_MODE", "warn")

    out = _run_session_audit_or_block(
        session_id="claude-web-test-scoped",
        agent="claude-web",
        entity_ids=["service:x"],
        defer_gaps=None,
    )

    assert scan_calls == [("claude-web-test-scoped", ("service:x",))]
    assert "warning" in out
    assert out["warning"]["audit_findings"] == [finding]
    assert "cortex.session.audit.unscoped" not in events
