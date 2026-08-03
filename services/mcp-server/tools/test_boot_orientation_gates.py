"""Boot orientation GATES strip, domain axis, sparse manifest."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._boot_helpers._briefing_card import render_briefing_card
from tools._boot_helpers._manifest import build_manifest
from tools._boot_helpers._orientation_blocks import (
    _seat_mcp_surface,
    render_orientation_blocks,
)
from tools.cortex_named_tools._boot_domain import (
    apply_domain_todo_state,
    default_boot_domain,
    extend_todo_fetch_params,
    life_lane_sentinel,
    life_suppressed,
    normalize_boot_domain,
)
from tools.cortex_named_tools._boot_runner import (
    _card_ceiling_for,
    _gate_only_skills_card,
)


def test_gates_strip_atop_orientation_stack() -> None:
    card, _ = render_briefing_card(family="claude", agent="claude-web")
    assert card.index("## GATES — fire BEFORE") < card.index("## Operator-facing duty")
    assert "Consult routing" in card
    assert "Capability verify (web)" in card


def test_cursor_orientation_thinned_to_gates_plus_rag() -> None:
    # Friction 25727 follow-on: cursor carries resident alwaysApply rules, so the
    # resident-covered doctrine blocks are dropped from the card. GATES stays
    # inline (fire-before-tool-call) and rag-scope stays (no resident/skill cover).
    blocks = render_orientation_blocks(
        family="claude", agent="claude-cursor", domain="coding"
    )
    joined = "\n".join(blocks)
    assert "## GATES — fire BEFORE" in joined
    assert "## RAG corpus retrieval" in joined
    # Resident-covered doctrine is absent on cursor.
    assert "## Operator-facing duty" not in joined
    assert "## MCP binding — connector-bound" not in joined
    assert "## MCP server primary" not in joined
    assert "## Dispatch & Consult" not in joined
    assert "## Git posture & liveness" not in joined
    assert "## Entity granularity" not in joined
    # Web-only blocks never appear on cursor.
    assert "Capability verify (web)" not in joined
    assert "## Session Close" not in joined


def test_web_orientation_retains_full_doctrine_coverage() -> None:
    # Web has no resident rules / native skill discovery — every doctrine block
    # MUST render inline (invariant: web asymmetry is load-bearing).
    blocks = render_orientation_blocks(family="claude", agent="claude-web")
    joined = "\n".join(blocks)
    for heading in (
        "## GATES — fire BEFORE",
        "## Operator-facing duty",
        "## MCP binding — connector-bound",
        "## MCP server primary",
        "## Dispatch & Consult",
        "## Consult routing",
        "## RAG corpus retrieval",
        "## Git posture & liveness",
        "## Entity granularity",
        "## Seat capability verify",
        "## Session Close",
    ):
        assert heading in joined, heading


def test_sparse_manifest_omits_zero_audit_row() -> None:
    manifest = build_manifest(
        plan_phases=None,
        in_flight_todos=None,
        todo_total=0,
        unread_count=0,
        reflective_total=0,
        recent_mentions=None,
        skills=None,
        audit_counters={"criticals": 0, "warnings": 0, "infos": 0},
    )
    assert not any(s.get("section") == "audit" for s in manifest)


def test_domain_coding_hard_excludes_life_todos() -> None:
    todos = [
        {
            "id": "todo:life",
            "title": "Life",
            "domain": "personal",
            "context": "personal",
        },
        {"id": "todo:code", "title": "Code", "domain": "infra", "context": "code"},
    ]
    ordered, sentinel = apply_domain_todo_state(
        todos, domain="coding", agent="claude-cursor", deadlines=[]
    )
    assert [t["id"] for t in ordered] == ["todo:code"]
    assert sentinel is None


def test_coding_brief_omits_life_sections_and_sentinel() -> None:
    card, _ = render_briefing_card(
        family="claude",
        agent="claude-cursor",
        domain="coding",
        life_suppressed=True,
        life_lane_sentinel=life_lane_sentinel(4),
        dropbox_files=["pending/a.pdf", "pending/b.pdf"],
        deadlines=[
            {
                "matter_id": "legal_matter:case-a",
                "deadline_date": "2026-08-01",
                "deadline_name": "Hearing",
                "matter_name": "Case A",
            }
        ],
        temporal_active=[
            {
                "entity_id": "legal_matter:case-a",
                "entity_name": "Case A",
                "claim": "Appeal window open",
            }
        ],
        todos=[{"id": "todo:code", "title": "Ship feature", "domain": "infra"}],
        todo_total=1,
    )
    assert "Dropbox Pending" not in card
    assert "## Deadlines" not in card
    assert "## Temporally Active" not in card
    assert "4 life-lane items hidden — explicit life brief to view" in card
    assert "## Todos" in card


def test_explicit_life_domain_renders_life_sections() -> None:
    card, _ = render_briefing_card(
        family="claude",
        agent="claude-cursor",
        domain="life",
        dropbox_files=["pending/a.pdf"],
        deadlines=[
            {
                "matter_id": "legal_matter:case-a",
                "deadline_date": "2026-08-01",
                "deadline_name": "Hearing",
                "matter_name": "Case A",
            }
        ],
        temporal_active=[
            {
                "entity_id": "legal_matter:case-a",
                "entity_name": "Case A",
                "claim": "Appeal window open",
            }
        ],
        todos=[{"id": "todo:life", "title": "Life task", "domain": "personal"}],
        todo_total=1,
    )
    assert "Dropbox Pending" in card
    assert "## Deadlines" in card
    assert "## Temporally Active" in card
    assert "life-lane items hidden" not in card


def test_cursor_coding_todo_fetch_excludes_life_domains() -> None:
    params: dict[str, str] = {}
    extend_todo_fetch_params("claude-cursor", params, domain="coding")
    assert params["context"] == "code"
    excluded = set(str(params["domain_exclude"]).split(","))
    assert "personal" in excluded
    assert "legal" in excluded
    assert "financial" in excluded


def test_life_suppressed_only_for_coding_lane() -> None:
    assert life_suppressed("coding")
    assert life_suppressed("code")
    assert not life_suppressed("life")
    assert not life_suppressed(None)
    assert not life_suppressed("coding", explicit_life_attach=True)


def test_web_life_domain_brief_unfiltered() -> None:
    card, _ = render_briefing_card(
        family="claude",
        agent="claude-web",
        domain="life",
        todos=[{"id": "todo:life", "title": "Life", "domain": "personal"}],
        todo_total=1,
    )
    assert "## Todos" in card
    assert "life-lane items hidden" not in card


def test_domain_coding_soft_reorder_and_sentinel() -> None:
    todos = [
        {
            "id": "todo:life",
            "title": "Life",
            "domain": "personal",
            "context": "personal",
        },
        {"id": "todo:code", "title": "Code", "domain": "infra", "context": "code"},
    ]
    ordered, sentinel = apply_domain_todo_state(
        todos, domain="life", agent="claude-web", deadlines=[]
    )
    assert ordered[0]["id"] == "todo:life"
    assert sentinel and "other-domain" in sentinel


def test_unspecified_domain_is_mixed_minimal() -> None:
    assert normalize_boot_domain(None) == "mixed-minimal"
    assert normalize_boot_domain("") == "mixed-minimal"


def test_render_orientation_blocks_accepts_domain_kwarg() -> None:
    blocks = render_orientation_blocks(
        family="claude", agent="claude-cursor", domain="coding"
    )
    joined = "\n".join(blocks)
    assert "## GATES — fire BEFORE" in joined


def test_boot_audit_block_ledger_splits_on_heading() -> None:
    from tools.cortex_named_tools._boot_audit_dump import _render_block_ledger

    card = "# Title\n\n## Section A\nfoo\n\n## Section B\nbar"
    ledger = _render_block_ledger(card)
    assert "## Section A" in ledger
    assert "## Section B" in ledger
    assert "**TOTAL**" in ledger


# ── friction 25727: cursor boot profile ──────────────────────────────────────


def test_default_boot_domain_cursor_defaults_coding() -> None:
    # Cursor is a code seat: unset domain becomes coding (life suppressed).
    assert default_boot_domain(None, "cursor") == "coding"
    # Explicit override is always preserved.
    assert default_boot_domain("life", "cursor") == "life"
    assert default_boot_domain("mixed-minimal", "cursor") == "mixed-minimal"


def test_default_boot_domain_non_cursor_passes_through() -> None:
    assert default_boot_domain(None, "web") is None
    assert default_boot_domain(None, "api") is None
    assert default_boot_domain("coding", "web") == "coding"


def test_gate_only_skills_card_cursor_header_native_discovery() -> None:
    # No skills_index_ref (cursor / IDE seats) → header points at native discovery,
    # never at a skills_index_ref that is always None for those seats.
    src = "## Agent Skills (12 active — concise manifest)\n> preamble\n- ⚑ `a` — g\n- `b` — x"
    out = _gate_only_skills_card(src, None)
    assert "native skill discovery" in out
    assert "skills_index_ref" not in out
    assert "skills-index-<seat>.md" not in out  # dead placeholder path is gone
    assert "⚑ `a`" in out
    assert "- `b`" not in out  # non-gate line dropped


def test_gate_only_skills_card_web_header_cites_index_ref() -> None:
    out = _gate_only_skills_card(
        "## Agent Skills (12 active)\n> preamble\n- ⚑ `a` — g",
        "notes/system/boot/skills-index-claude-web.md",
    )
    assert "skills_index_ref" in out


# ── thread 6310 / todo:life-mcp-story-wire-update: dual-endpoint card coherence ──


def _web_orientation() -> str:
    return "\n".join(render_orientation_blocks(family="claude", agent="claude-web"))


def _code_extra() -> frozenset[str]:
    """Primaries the life mount does not carry — same derivation the card uses."""
    from endpoint_surface import derive_code_extra_primary_tools

    return derive_code_extra_primary_tools()


def test_seat_mcp_surface_derives_from_agents_yaml() -> None:
    # The card's endpoint predicate is agents.yaml `mcp_surface`, not the boot
    # `domain` axis and not the mount that happened to render the card.
    assert _seat_mcp_surface("claude-web") == "life"
    assert _seat_mcp_surface("grok-web") == "life"
    assert _seat_mcp_surface("claude-cursor") == "code"
    assert _seat_mcp_surface("grok-api") == "code"


def test_life_manifest_line_lists_life_primaries_only() -> None:
    # The unified manifest advertised the code-infra family on a life boot
    # (N=18 union). The line must now be the life mount's own tools/list.
    from endpoint_surface import derive_surface_primary_tools

    joined = _web_orientation()
    line = joined[joined.index("## MCP server primary") :].split("\n")[1]
    assert "/mcp/life" in joined
    for name in sorted(derive_surface_primary_tools("life")):
        assert f"{name}" in line, name
    for name in sorted(_code_extra()):
        assert name not in line, name


def test_life_dispatch_block_delegates_code_extra_over_the_bus() -> None:
    joined = _web_orientation()
    block = joined[joined.index("## Dispatch & Consult") :].split("\n\n")[0]
    # Surface-gate salience: CODE_EXTRA named as a REAL absence, with the GATES §1
    # carve-out spelled out (call-by-name is what kept the seat probing).
    for name in sorted(_code_extra()):
        assert f"`{name}`" in block, name
    assert "absence is REAL" in block
    assert "GATES §1" in block
    # Sanctioned transport present; direct-call prescription absent.
    assert 'agent_bus(tool="request", to="cursor"' in block
    assert "life-to-code-request-lane" in block
    assert "call directly" not in block
    assert "team_dispatch(op=" not in block


def test_code_seat_dispatch_block_keeps_direct_call_form() -> None:
    # Code-surface seats (api here; cursor renders no dispatch block at all) keep
    # the direct-call shapes — the life fix must not disarm the code lane.
    joined = "\n".join(render_orientation_blocks(family="grok", agent="grok-api"))
    assert "team_dispatch(op=generate" in joined
    assert "`team_dispatch`/`panel_dispatch` are server-primary" in joined
    assert "/mcp/code" in joined
    assert "delegate, ¬ dispatch" not in joined


def test_life_card_carries_no_direct_dispatch_instruction() -> None:
    # Acceptance-level: the delivered web card, not just the block list.
    card, _ = render_briefing_card(family="claude", agent="claude-web", domain="life")
    assert "## MCP server primary — `/mcp/life`" in card
    assert "`team_dispatch`/`panel_dispatch` are server-primary" not in card
    assert 'agent_bus(tool="request", to="cursor"' in card


def test_cursor_card_unaffected_by_surface_split() -> None:
    # Cursor's thinned set (friction 25727) renders neither endpoint-dependent
    # body, so the split must be invisible there.
    joined = "\n".join(
        render_orientation_blocks(family="claude", agent="claude-cursor", domain="coding")
    )
    assert "/mcp/life" not in joined
    assert "/mcp/code" not in joined
    assert "## Dispatch & Consult" not in joined


def test_card_ceiling_is_platform_and_lane_aware() -> None:
    # Cursor coding lane (the default) gets the tight regression tripwire.
    assert _card_ceiling_for("claude-cursor", "coding") == 12_000
    # Explicit life/mixed cursor brief carries more → default ceiling, no false trip.
    assert _card_ceiling_for("claude-cursor", "life") == 15_500
    assert _card_ceiling_for("claude-cursor", "mixed-minimal") == 15_500
    assert _card_ceiling_for("claude-web", "coding") == 19_000
    assert _card_ceiling_for("grok-api", "coding") == 15_500
