"""Recipient alias expansion for agent-bus inbox matching."""

from __future__ import annotations

from agent_seat.registry import expand_recipient_slugs, normalize_agent_slug


def test_normalize_web_to_claude_web() -> None:
    assert normalize_agent_slug("web") == "claude-web"


def test_normalize_cursor_consult_role() -> None:
    assert normalize_agent_slug("cursor-consult") == "cursor-consult"


def test_normalize_cursor_claude_nickname_to_claude_cursor() -> None:
    assert normalize_agent_slug("cursor-claude") == "claude-cursor"
    assert normalize_agent_slug("web-claude") == "claude-web"


def test_expand_recipient_includes_legacy_web() -> None:
    expanded = expand_recipient_slugs("claude-web")
    assert "web" in expanded
    assert "claude-web" in expanded


def test_persona_aliases_not_normalized() -> None:
    """Legacy persona slugs are retired — use canonical role slugs."""
    assert normalize_agent_slug("oppie") == "oppie"
    assert normalize_agent_slug("orion") == "orion"
    assert normalize_agent_slug("bard") == "bard"
    assert normalize_agent_slug("forge") == "forge"


def test_cursor_orion_not_normalized() -> None:
    """Retired seat alias — use gpt-cursor or gpt_cursor."""
    assert normalize_agent_slug("cursor_orion") == "cursor_orion"
    assert normalize_agent_slug("gpt_cursor") == "gpt-cursor"


def test_all_canonical_seats_and_roles_normalize_closed() -> None:
    """∀ agents.yaml cell + role: canonical and underscore spellings resolve."""
    from agent_seat.profiles import load_profiles, load_roles

    for family, platform in load_profiles():
        slug = f"{family}-{platform}"
        assert normalize_agent_slug(slug) == slug
        assert normalize_agent_slug(slug.replace("-", "_")) == slug
    for role in load_roles():
        assert normalize_agent_slug(role) == role
        assert normalize_agent_slug(role.replace("-", "_")) == role


def test_injected_roster_drift_resolves_without_hand_edit(
    tmp_path, monkeypatch
) -> None:
    """RED on hand-maintained map: a brand-new role + seat cell resolve purely
    from the agents.yaml SOT. Fixture: minimal agents.yaml with role
    'probe-role' (default newfam/web) and profile cell 'newfam/web'."""
    fixture = tmp_path / "agents.yaml"
    fixture.write_text(
        """\
lead_seats:
  - newfam-web

profiles:
  newfam/web:
    provider: anthropic
    default_model: null
    tool_surface: mcp
    delivery: manual
    dispatchable: false
    include_deadlines: false
    include_review_queue: false
    confirm_and_proceed: false
    addenda: []

roles:
  probe-role:
    description: probe
    default_family: newfam
    default_platform: web
    default_model: null
""",
        encoding="utf-8",
    )
    from agent_seat import profiles, registry

    monkeypatch.setattr(profiles, "_AGENTS_YAML", fixture)
    profiles.load_profiles.cache_clear()
    profiles.load_roles.cache_clear()
    profiles.load_lead_agent_slugs.cache_clear()
    registry._dispatch_aliases.cache_clear()
    try:
        assert registry.normalize_agent_slug("probe_role") == "probe-role"
        assert registry.normalize_agent_slug("Newfam-Web") == "newfam-web"
    finally:
        profiles.load_profiles.cache_clear()
        profiles.load_roles.cache_clear()
        profiles.load_lead_agent_slugs.cache_clear()
        registry._dispatch_aliases.cache_clear()


def test_legacy_residue_not_derivable() -> None:
    """∀ k ∈ _LEGACY_ALIASES: k is not the normalization of any canonical slug
    (creep guard — mirrors the import-time RuntimeError)."""
    from agent_seat import registry
    from agent_seat.profiles import load_profiles, load_roles

    derivable = {f"{f}-{p}".replace("-", "_") for f, p in load_profiles()}
    derivable |= {r.replace("-", "_") for r in load_roles()}
    assert not (registry._LEGACY_ALIASES.keys() & derivable)


def test_gemini_web_derived_not_legacy() -> None:
    assert normalize_agent_slug("gemini_web") == "gemini-web"


def test_subagent_subagent_derived() -> None:
    assert normalize_agent_slug("subagent_subagent") == "subagent-subagent"
