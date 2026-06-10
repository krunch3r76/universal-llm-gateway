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


def test_block_ledger_totals_match_card_bytes() -> None:
    from tools.cortex_named_tools._boot_audit_dump import _render_block_ledger

    card = "# Boot Briefing — t\n\n## A\nalpha—beta\n\n## B\ngamma\n"
    ledger = _render_block_ledger(card)
    assert f"**{len(card.encode('utf-8'))}**" in ledger
    assert "| ## A |" in ledger and "| ## B |" in ledger
