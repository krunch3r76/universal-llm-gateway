"""Stage-A deterministic precision-gate tests (thread 1881 reviewer verdict).

Drops tail candidates whose only evidence is a generic singleton token,
unless a contiguous multi-token phrase matched or >=2 specific terms matched.
The gate reads base match metadata only — required-gate / delivery-priority
boosts must not rescue generic bleed.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from cortex_store.routes._skill_suggest import run_stage_a

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    source_uri TEXT,
    lifecycle TEXT,
    attributes TEXT
);
"""

# Realistic handoff/dispatch context with INCIDENTAL generic tokens
# (lead, seat, consult) that previously bled lead-seat-boot / advisor-timing.
_HANDOFF_CONTEXT = (
    "authoring a handoff packet and firing team_dispatch dispatch, weighing a "
    "consensus panel and steelman review; lead seat consult"
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    source_uri: str,
    trigger_match_terms: list[str],
    boot_importance: str | None = None,
    delivery_priority: int = 100,
    applicable_agents: list[str] | None = None,
    description: str | None = None,
    trigger_short: str = "",
) -> None:
    agents = applicable_agents if applicable_agents is not None else ["claude-web"]
    attrs: dict[str, object] = {
        "applicable_agents": agents,
        "trigger_match_terms": trigger_match_terms,
        "delivery_priority": delivery_priority,
    }
    if boot_importance:
        attrs["boot_importance"] = boot_importance
    if trigger_short:
        attrs["trigger_short"] = trigger_short
    conn.execute(
        "INSERT INTO entities (id, type, name, description, source_uri, lifecycle, attributes) "
        "VALUES (?, 'agent_skill', ?, ?, ?, 'active', ?)",
        (
            entity_id,
            entity_id.removeprefix("agent_skill:"),
            description,
            source_uri,
            json.dumps(attrs),
        ),
    )


def _handoff_conn() -> sqlite3.Connection:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:consult-routing",
        source_uri="agent-skills/consult-routing.md",
        trigger_match_terms=[
            "consult",
            "handoff",
            "team_dispatch",
            "dispatch",
            "panel",
        ],
        description="Route consult and handoff requests.",
    )
    _insert(
        conn,
        "agent_skill:handoff-packet-authoring",
        source_uri="agent-skills/handoff-packet-authoring.md",
        trigger_match_terms=["packet", "handoff", "team_dispatch"],
        description="Author six-block handoff packets for dispatch.",
    )
    _insert(
        conn,
        "agent_skill:consensus-steelman-posture",
        source_uri="agent-skills/consensus-steelman-posture.md",
        trigger_match_terms=["steelman", "panel", "consensus"],
        description="Run consensus panels with steelman posture.",
    )
    _insert(
        conn,
        "agent_skill:lead-seat-boot",
        source_uri="agent-skills/lead-seat-boot.md",
        trigger_match_terms=["lead", "seat", "boot", "lead seat boot"],
        boot_importance="required_gate",
        delivery_priority=5,
    )
    _insert(
        conn,
        "agent_skill:advisor-timing",
        source_uri="agent-skills/advisor-timing.md",
        trigger_match_terms=["consult", "decision", "pre-edit"],
        boot_importance="required_gate",
        delivery_priority=5,
    )
    conn.commit()
    return conn


def _run(conn: sqlite3.Connection, context: str, *, limit: int = 8) -> dict:
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        return run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context=context,
            limit=limit,
        )


@pytest.mark.offline
def test_stage_a_filters_generic_singleton_tail_for_handoff_dispatch_context() -> None:
    result = _run(_handoff_conn(), _HANDOFF_CONTEXT)
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "lead-seat-boot" not in slugs
    assert "advisor-timing" not in slugs


@pytest.mark.offline
def test_stage_a_preserves_genuine_handoff_dispatch_top3_after_precision_gate() -> None:
    result = _run(_handoff_conn(), _HANDOFF_CONTEXT)
    slugs = [s["slug"] for s in result["suggestions"]]
    omitted = {item["slug"] for item in result["omitted"]}
    assert "consult-routing" in omitted
    assert "consult-routing" not in slugs
    assert set(slugs[:2]) == {
        "handoff-packet-authoring",
        "consensus-steelman-posture",
    }


@pytest.mark.offline
def test_stage_a_phrase_match_can_pass_precision_gate_for_required_gate_skill() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:lead-seat-boot",
        source_uri="agent-skills/lead-seat-boot.md",
        trigger_match_terms=["lead", "seat", "boot", "lead seat"],
        boot_importance="required_gate",
        applicable_agents=["claude-cursor"],
    )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-cursor",
            loaded=[],
            conversation_context="lead seat boot protocol setup",
            limit=8,
        )
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "lead-seat-boot" in slugs


@pytest.mark.offline
def test_stage_a_boosts_do_not_rescue_generic_singleton_matches() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:advisor-timing",
        source_uri="agent-skills/advisor-timing.md",
        trigger_match_terms=["consult", "decision", "pre-edit"],
        boot_importance="required_gate",
        delivery_priority=1,
    )
    conn.commit()
    result = _run(conn, "consult")
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "advisor-timing" not in slugs


@pytest.mark.offline
def test_suggestions_expose_description_not_tags() -> None:
    result = _run(_handoff_conn(), _HANDOFF_CONTEXT)
    assert result["suggestions"]
    for item in result["suggestions"]:
        assert item.get("description")
        assert "trigger_match" not in item
        assert "trigger_match_terms" not in item
        assert item["reason"] == item["description"]


@pytest.mark.offline
def test_rerank_enabled_by_default() -> None:
    import os

    from cortex_store.skill_suggest_rank import rerank_enabled_default

    key = "SKILL_SUGGEST_RERANK_ENABLED"
    old = os.environ.pop(key, None)
    try:
        assert rerank_enabled_default() is True
    finally:
        if old is not None:
            os.environ[key] = old


def _generic_phrase_conn() -> sqlite3.Connection:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:lead-seat-boot",
        source_uri="agent-skills/lead-seat-boot.md",
        trigger_match_terms=["lead", "seat", "lead seat"],
        boot_importance="required_gate",
        delivery_priority=5,
        applicable_agents=["claude-cursor"],
    )
    conn.commit()
    return conn


@pytest.mark.offline
def test_stage_a_all_generic_phrase_dropped_when_non_contiguous() -> None:
    # 'lead' and 'seat' present but NOT contiguous — the multi-word generic
    # phrase 'lead seat' must not masquerade as a specific match.
    result = _run(_generic_phrase_conn(), "the lead engineer works from a driver seat")
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "lead-seat-boot" not in slugs


@pytest.mark.offline
def test_stage_a_all_generic_phrase_kept_when_contiguous() -> None:
    with patch(
        "cortex_store.routes._skill_suggest.cortex_conn",
        return_value=_generic_phrase_conn(),
    ):
        result = run_stage_a(
            agent="claude-cursor",
            loaded=[],
            conversation_context="lead seat assignment for the crew",
            limit=8,
        )
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "lead-seat-boot" in slugs


# Labeled corpus (thread 1876): bleed vs legitimate advisor-timing surfaces.
# Verdict: add `decision` to generic singletons; multi-token terms require all
# tokens non-generic (prevents 'decision point' masquerading via 'point').
_ADVISOR_TIMING_CORPUS: list[tuple[str, str, bool]] = [
    (
        "bleed_court_decision",
        "The court's decision in Mansubi v County was final; no further appeal window",
        False,
    ),
    (
        "bleed_narrative_decision",
        "The operator made a decision to proceed with the refactor yesterday",
        False,
    ),
    (
        "bleed_decision_entity",
        "seed a decision entity in cortex with claim and evidence",
        False,
    ),
    (
        "bleed_parsing_decision",
        "unexpected token at decision point in the YAML parser",
        False,
    ),
    ("bleed_decision_only", "decision", False),
    (
        "borderline_handoff_consult",
        "weighing a decision on which transport to use for this handoff consult",
        False,
    ),
    (
        "legit_pre_edit",
        "about to call team_dispatch — read advisor-timing skill before first write pre-edit",
        True,
    ),
]


@pytest.mark.offline
@pytest.mark.parametrize("label,context,expect", _ADVISOR_TIMING_CORPUS)
def test_stage_a_advisor_timing_labeled_corpus(
    label: str, context: str, expect: bool
) -> None:
    del label  # corpus id for failure messages only
    result = _run(_handoff_conn(), context)
    got = "advisor-timing" in {s["slug"] for s in result["suggestions"]}
    assert got is expect


@pytest.mark.offline
def test_web_seat_preloaded_uses_boot_card_channels_not_registry() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:cortex-orientation",
        source_uri="agent-skills/cortex-orientation.md",
        trigger_match_terms=["cortex", "boot", "entity_get"],
    )
    _insert(
        conn,
        "agent_skill:cortex-provenance-discipline",
        source_uri="agent-skills/cortex-provenance-discipline.md",
        trigger_match_terms=["cortex", "cite", "quote"],
    )
    _insert(
        conn,
        "agent_skill:boot-execution-discipline",
        source_uri="agent-skills/boot-execution-discipline.md",
        trigger_match_terms=["cortex", "boot"],
    )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="walk kaywan cortex graph for job fit",
            limit=8,
        )
    preloaded = set(result["seat_preloaded"])
    assert "cortex-orientation" not in preloaded
    assert "cortex-provenance-discipline" not in preloaded
    assert "orchestrator-core" not in preloaded
    assert "operator-posture" in preloaded
    suggested = {s["slug"] for s in result["suggestions"]}
    assert "cortex-orientation" in suggested or "cortex-provenance-discipline" in suggested


@pytest.mark.offline
def test_web_loaded_set_unions_orientation_and_opcontext_channels() -> None:
    """Channel-2/3 slugs suppress suggestions; index-only controls still surface."""
    from agent_seat.inject_channels import (
        web_opcontext_inject_skill_slugs,
        web_orientation_inject_skill_slugs,
    )

    conn = _conn()
    channel_slugs = tuple(
        dict.fromkeys(
            (
                *web_orientation_inject_skill_slugs("claude-web"),
                *web_opcontext_inject_skill_slugs("claude-web", "claude", "web"),
            )
        )
    )
    for slug in channel_slugs:
        terms = [slug.replace("-", " "), slug]
        if slug == "model-tier-awareness-web":
            terms.extend(["model tier", "tier"])
        _insert(
            conn,
            f"agent_skill:{slug}",
            source_uri=f"agent-skills/{slug}.md",
            trigger_match_terms=terms,
        )
    _insert(
        conn,
        "agent_skill:boot-execution-discipline",
        source_uri="agent-skills/boot-execution-discipline.md",
        trigger_match_terms=["boot", "execution", "discipline", "cortex boot"],
    )
    conn.commit()
    context = (
        "operator posture consult routing git posture entity lifecycle session close "
        "model tier frontier reasoning prose discipline team dispatch boot execution"
    )
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context=context,
            limit=20,
        )
    suggested = {s["slug"] for s in result["suggestions"]}
    omitted = {item["slug"] for item in result["omitted"]}
    assert "boot-execution-discipline" in suggested
    assert "operator-posture" in omitted
    assert "consult-routing" in omitted
    assert "model-tier-awareness-web" in omitted
    assert "frontier-reasoning-discipline" in omitted
    assert "operator-posture" not in suggested
    assert "consult-routing" not in suggested


@pytest.mark.offline
def test_cursor_seat_has_no_seat_preloaded() -> None:
    with patch(
        "cortex_store.routes._skill_suggest.cortex_conn",
        return_value=_handoff_conn(),
    ):
        result = run_stage_a(
            agent="claude-cursor",
            loaded=[],
            conversation_context="handoff packet team_dispatch",
            limit=8,
        )
    assert result["seat_preloaded"] == []


@pytest.mark.offline
def test_stage_a_suggests_subgraph_render_on_walk_graph_context() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:subgraph-render",
        source_uri="agent-skills/subgraph-render.md",
        trigger_match_terms=[
            "walk graph",
            "walk_subgraph",
            "graph navigation",
            "hub entity",
            "employment history",
        ],
    )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=["cortex-orientation", "cortex-provenance-discipline"],
            conversation_context=(
                "Walk person:kaywan-mansubi cortex graph for employment history "
                "and job fit"
            ),
            limit=8,
        )
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "subgraph-render" in slugs


@pytest.mark.offline
def test_related_skills_boost_surfaces_subgraph_render_when_orientation_loaded() -> (
    None
):
    conn = _conn()
    _insert(
        conn,
        "agent_skill:subgraph-render",
        source_uri="agent-skills/subgraph-render.md",
        trigger_match_terms=["render_subgraph", "hub entity"],
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, lifecycle, attributes) "
        "VALUES (?, 'agent_skill', ?, ?, 'active', ?)",
        (
            "agent_skill:cortex-orientation",
            "cortex-orientation",
            "agent-skills/cortex-orientation.md",
            json.dumps(
                {
                    "applicable_agents": ["claude-web"],
                    "trigger_match_terms": ["cortex"],
                    "related_skills": ["subgraph-render"],
                }
            ),
        ),
    )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=["cortex-orientation"],
            conversation_context="hub entity canvas via render_subgraph after cortex read",
            limit=8,
        )
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "subgraph-render" in slugs
    subgraph = next(s for s in result["suggestions"] if s["slug"] == "subgraph-render")
    assert subgraph["score"] >= 2.0  # base 2 terms + related_skills companion boost


@pytest.mark.offline
def test_coding_session_start_returns_bundle_not_session_close() -> None:
    conn = _conn()
    advertise_slugs = (
        "implement-work-item",
        "git-posture",
        "service-lifecycle",
        "completion-provenance-discipline",
        "fs",
    )
    for slug in (
        "session-close",
        *advertise_slugs,
        "architecture-invariants",
        "ulg-architecture_ulg",
        "orchestrator-workflow",
    ):
        _insert(
            conn,
            f"agent_skill:{slug}",
            source_uri=f"agent-skills/{slug}.md",
            trigger_match_terms=["session", "close"]
            if slug == "session-close"
            else [slug],
            boot_importance="required_gate" if slug == "session-close" else None,
            delivery_priority=0 if slug == "session-close" else 100,
            applicable_agents=["claude-cursor"] if slug == "git-posture" else ["*"],
        )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="coding session: implement repo parity",
            limit=8,
        )
    slugs = [s["slug"] for s in result["suggestions"]]
    assert "session-close" not in slugs
    web_advertise = [slug for slug in advertise_slugs if slug != "git-posture"]
    assert set(slugs) == set(web_advertise) | {
        "architecture-invariants",
        "ulg-architecture_ulg",
        "orchestrator-workflow",
    }
    assert "git-posture" not in slugs
    for item in result["suggestions"]:
        assert item["score"] == 100.0
        assert "coding session" in item["reason"].lower()
        assert "trigger_match" not in item
    preloaded = set(result["seat_preloaded"])
    assert "cortex-orientation" not in preloaded
    assert "orchestrator-core" not in preloaded
    assert "operator-posture" in preloaded


@pytest.mark.offline
def test_coding_session_bundle_cursor_only_member_withheld_from_web() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:git-posture",
        source_uri="agent-skills/git-posture.md",
        trigger_match_terms=["git-posture"],
        applicable_agents=["claude-cursor"],
    )
    for slug in (
        "implement-work-item",
        "service-lifecycle",
        "completion-provenance-discipline",
        "fs",
    ):
        _insert(
            conn,
            f"agent_skill:{slug}",
            source_uri=f"agent-skills/{slug}.md",
            trigger_match_terms=[slug],
            applicable_agents=["*"],
        )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="coding session: modify a service in the repo",
            limit=8,
        )
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "git-posture" not in slugs


@pytest.mark.offline
def test_universal_non_bundle_skill_included_for_web_general_discovery() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:universal-only",
        source_uri="agent-skills/universal-only.md",
        trigger_match_terms=["handoff", "dispatch", "packet"],
        applicable_agents=["*"],
    )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="authoring a handoff packet and firing team_dispatch",
            limit=8,
        )
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "universal-only" in slugs


@pytest.mark.offline
def test_coding_session_start_git_posture_carries_md_list_nudge() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:git-posture",
        source_uri=(
            "workspaces://universal-llm-gateway/.cursor/skills/git-posture/SKILL.md"
        ),
        trigger_match_terms=["git-posture", "git status", "uncommitted"],
        applicable_agents=["*"],
    )
    for slug in (
        "implement-work-item",
        "service-lifecycle",
        "completion-provenance-discipline",
        "fs",
    ):
        _insert(
            conn,
            f"agent_skill:{slug}",
            source_uri=f"agent-skills/{slug}.md",
            trigger_match_terms=[slug],
            applicable_agents=["*"],
        )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-cursor",
            loaded=[],
            conversation_context="coding session: implement repo parity",
            limit=8,
        )
    git_posture = next(s for s in result["suggestions"] if s["slug"] == "git-posture")
    reason = git_posture["reason"]
    assert "coding session" in reason.lower()
    assert "md_list" in reason
    assert "Execution lanes" in reason
    assert "Commit posture" in reason
    assert "What not to infer" in reason


@pytest.mark.offline
def test_coding_session_start_surfaces_advertise_slugs_for_cursor() -> None:
    conn = _conn()
    advertise_slugs = (
        "implement-work-item",
        "git-posture",
        "service-lifecycle",
        "completion-provenance-discipline",
        "fs",
    )
    for slug in advertise_slugs:
        _insert(
            conn,
            f"agent_skill:{slug}",
            source_uri=f"agent-skills/{slug}.md",
            trigger_match_terms=[slug],
            applicable_agents=["*"],
        )
    conn.commit()
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-cursor",
            loaded=[],
            conversation_context="coding session: modify a service in the repo",
            limit=8,
        )
    slugs = {s["slug"] for s in result["suggestions"]}
    assert set(advertise_slugs).issubset(slugs)
