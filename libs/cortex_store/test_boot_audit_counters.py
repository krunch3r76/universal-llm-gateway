"""GET /boot-audit-counters route tests."""

from __future__ import annotations

from unittest.mock import patch

from cortex_store.routes.boot.audit_counters import boot_audit_counters


def test_boot_audit_counters_strips_findings() -> None:
    with patch(
        "cortex_store.routes.boot.audit_counters._op_audit",
        return_value={
            "findings": [{"kind": "x", "severity": "critical"}] * 5000,
            "gap_count": 5000,
            "criticals": 3,
            "warnings": 1,
            "infos": 2,
            "duration_ms": 42,
            "kinds_run": ["orphan_entity"],
        },
    ):
        payload = boot_audit_counters()
    assert payload == {
        "criticals": 3,
        "warnings": 1,
        "infos": 2,
        "gap_count": 5000,
        "duration_ms": 42,
    }
    assert "findings" not in payload


def test_boot_audit_counters_degrades_on_error() -> None:
    with patch(
        "cortex_store.routes.boot.audit_counters._op_audit",
        return_value={"error": "boom", "code": "fail"},
    ):
        payload = boot_audit_counters()
    assert payload["unavailable"] is True
    assert payload["error"] == "boom"
    assert "criticals" not in payload
    assert "warnings" not in payload
    assert "infos" not in payload
