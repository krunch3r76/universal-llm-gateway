"""extract_boot_results audit counter handling."""

from __future__ import annotations

from tools.cortex_named_tools._boot_data_fetch import (
    build_futures_spec,
    extract_boot_results,
)


class _StubRecorder:
    def wrap(self, _name: object, fn: object) -> object:
        return fn


def test_build_futures_spec_includes_rules_layer_fetch() -> None:
    spec = build_futures_spec("claude-cursor", {}, _StubRecorder())
    assert "rules" in spec
    url = spec["rules"][2]
    assert "layer=rules" in url
    assert "for_agent=claude-cursor" in url


def test_extract_boot_results_separates_rules_from_skills() -> None:
    extracted = extract_boot_results(
        "claude-cursor",
        {
            "sessions": [],
            "threads": {"threads": []},
            "unread_turns": {"turns": []},
            "skills": [{"id": "agent_skill:a"}],
            "rules": [{"id": "rule:x"}],
        },
        {},
    )
    assert extracted["skills"] == [{"id": "agent_skill:a"}]
    assert extracted["rules"] == [{"id": "rule:x"}]


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
