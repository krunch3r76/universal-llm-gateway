"""extract_boot_results audit counter handling."""

from __future__ import annotations

from tools.cortex_named_tools._boot_data_fetch import (
    build_futures_spec,
    extract_boot_results,
)


class _StubRecorder:
    def wrap(self, _name: object, fn: object) -> object:
        return fn


def test_build_futures_spec_omits_rules_layer_fetch() -> None:
    spec = build_futures_spec("claude-cursor", {}, _StubRecorder())
    assert "rules" not in spec
    assert "layer=rules" not in str(spec)


def test_build_futures_spec_skills_uses_skills_view_boot() -> None:
    spec = build_futures_spec("claude-web", {}, _StubRecorder())
    assert "skills" in spec
    url = spec["skills"][2]
    assert "/skills?" in url
    assert "view=boot" in url
    assert "render=concise" in url
    assert "for_agent=claude-web" in url
    assert "/boot-skills" not in url


def test_extract_boot_results_skills_only() -> None:
    extracted = extract_boot_results(
        "claude-cursor",
        {
            "sessions": [],
            "threads": {"threads": []},
            "unread_turns": {"turns": []},
            "skills": {"items": [{"id": "agent_skill:a"}], "unpartitioned_count": 0},
        },
        {},
    )
    assert extracted["skills"] == [{"id": "agent_skill:a"}]
    assert "rules" not in extracted


def test_extract_boot_results_skills_concise_markdown() -> None:
    extracted = extract_boot_results(
        "claude-web",
        {
            "sessions": [],
            "threads": {"threads": []},
            "unread_turns": {"turns": []},
            "skills": {
                "items": [{"id": "agent_skill:a"}],
                "unpartitioned_count": 0,
                "rendered": {"concise_markdown": "# index\n"},
            },
        },
        {},
    )
    assert extracted["skills_concise_markdown"] == "# index\n"


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
