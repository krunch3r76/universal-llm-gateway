"""extract_boot_results audit counter handling."""

from __future__ import annotations

from tools.cortex_named_tools._boot_data_fetch import extract_boot_results


def test_extract_boot_results_omits_audit_counters_when_unavailable() -> None:
    extracted = extract_boot_results(
        "claude-cursor",
        {
            "sessions": [],
            "threads": {"threads": []},
            "unread_turns": {"turns": []},
            "audit": {"unavailable": True, "error": "boom"},
        },
        {},
    )
    assert extracted["audit_counters"] is None
