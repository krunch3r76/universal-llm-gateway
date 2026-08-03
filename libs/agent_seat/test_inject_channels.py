"""Inject-channel map gating tests."""

from __future__ import annotations

import pytest

from agent_seat.inject_channels import (
    ORIENTATION_BLOCK_SKILL_MAP,
    opcontext_section_keys_for_agent,
    orientation_block_keys_for_agent,
    web_opcontext_inject_skill_slugs,
    web_orientation_inject_skill_slugs,
    web_seat_injected_skill_slugs,
)


@pytest.mark.offline
def test_web_orientation_includes_web_only_blocks() -> None:
    web_slugs = set(web_orientation_inject_skill_slugs("claude-web"))
    cursor_slugs = set(web_orientation_inject_skill_slugs("claude-cursor"))
    assert "session-close" in web_slugs
    assert "session-close" not in cursor_slugs
    # Web renders operator-posture doctrine inline only (skill is cursor_only —
    # ¬ Customize chip / ¬ inject-channel slug accounting).
    assert "operator-posture" not in web_slugs
    # Cursor (friction 25727 follow-on) renders only the thinned resident-covered
    # set; its one block (rag-scope) has no backing skill → no injected slugs.
    assert cursor_slugs == set()


@pytest.mark.offline
def test_cursor_orientation_selection_is_thinned_to_rag_only() -> None:
    cursor_keys = orientation_block_keys_for_agent("claude-cursor")
    assert cursor_keys == frozenset({"rag-scope-awareness-block"})
    # Doctrine covered by resident alwaysApply rules is dropped on cursor.
    for dropped in (
        "operator-posture-block",
        "mcp-binding-block",
        "mcp-server-primary-block",
        "dispatch-consult-block",
        "consult-routing-gate-block",
        "liveness-block",
        "entity-hierarchy-block",
    ):
        assert dropped not in cursor_keys


@pytest.mark.offline
def test_web_and_api_orientation_selection_carry_full_doctrine() -> None:
    web_keys = orientation_block_keys_for_agent("claude-web")
    api_keys = orientation_block_keys_for_agent("claude-api")
    full_doctrine = {
        "operator-posture-block",
        "mcp-binding-block",
        "mcp-server-primary-block",
        "dispatch-consult-block",
        "consult-routing-gate-block",
        "rag-scope-awareness-block",
        "liveness-block",
        "entity-hierarchy-block",
    }
    assert full_doctrine <= web_keys
    assert full_doctrine <= api_keys
    # Web-only blocks ride on web, never on api (api has no resident rules but
    # also no claude.ai connector lifecycle / session-close-kernel surface).
    assert {"session-close-web-block"} <= web_keys
    assert "session-close-web-block" not in api_keys


@pytest.mark.offline
def test_opcontext_excludes_subagent_sections() -> None:
    keys = opcontext_section_keys_for_agent("subagent", "subagent")
    assert "frontier-reasoning" not in keys
    assert "team-consultation" not in keys
    assert "prose-discipline" in keys
    slugs = web_opcontext_inject_skill_slugs(None, "subagent", "subagent")
    assert "frontier-reasoning-discipline" not in slugs
    assert "prose-discipline" in slugs


@pytest.mark.offline
def test_web_seat_injected_unions_channel_one_live() -> None:
    slugs = set(web_seat_injected_skill_slugs("claude-web"))
    # Channel 2 (orientation) + channel 3 (opctx) union — inject-registry slugs
    # (e.g. cortex-orientation) are a separate delivery path, not this union.
    # operator-posture is cursor_only — inline doctrine only, ¬ channel slug.
    assert "operator-posture" not in slugs
    assert "frontier-reasoning-discipline" in slugs
    assert "consult-routing" in slugs


@pytest.mark.offline
def test_orientation_block_map_has_expected_web_blocks() -> None:
    web_keys = orientation_block_keys_for_agent("claude-web")
    assert "session-close-web-block" in web_keys
    assert ORIENTATION_BLOCK_SKILL_MAP["dispatch-consult-block"] == (
        "consult-routing",
        "dispatch-shape",
    )
