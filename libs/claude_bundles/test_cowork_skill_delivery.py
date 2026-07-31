"""Unit tests for Cowork skill delivery (friction 24594)."""

from __future__ import annotations

import pytest

from claude_bundles.cowork_skill_delivery import (
    SkillDeliveryError,
    attest_delivery_channels,
    attest_injected_slugs,
    attest_skills_chip_enabled,
    classify_skill_delivery,
    github_cannot_load_skill_trees_note,
    ledger_skills_channels,
    ledger_skills_record,
    load_skill_bodies,
    partition_cdp_skills,
    prepend_cdp_dispatch_skills,
    prepend_injected_skills,
    render_injected_skills_block,
)


def test_cursor_only_classifies_as_inject() -> None:
    plan = classify_skill_delivery("claude-ai-cdp-navigation")
    assert plan.surface_class == "cursor_only"
    assert plan.channel == "inject"
    assert plan.sot_path is not None


def test_shared_sync_classifies_as_customize() -> None:
    plan = classify_skill_delivery("reasoning-posture")
    assert plan.surface_class == "shared_sync"
    assert plan.channel == "customize_skills"


def test_is_claude_slug_shared_sync_only() -> None:
    from claude_bundles.cowork_skill_delivery import is_claude_slug

    assert is_claude_slug("reasoning-posture") is True
    assert is_claude_slug("claude-ai-cdp-navigation") is False


def test_partition_path_sim_inlines_not_attaches() -> None:
    slash, inline = partition_cdp_skills(["path-sim", "reasoning-posture"])
    assert slash == ["reasoning-posture"]
    assert inline == ["path-sim"]
    non_shared = set(inline)
    assert set(slash).isdisjoint(non_shared)

    prompt, used_slash, bodies = prepend_cdp_dispatch_skills(
        "## Task\n",
        ["path-sim", "reasoning-posture"],
    )
    assert used_slash == ["reasoning-posture"]
    assert bodies[0].slug == "path-sim"
    assert prompt.startswith("/reasoning-posture\n")
    assert "/path-sim" not in prompt.split("<skills_inline>", 1)[0]
    assert '<skill slug="path-sim"' in prompt
    assert "<!--cdp-required-skills:path-sim,reasoning-posture-->" in prompt


def test_required_authority_survives_inline_drop() -> None:
    """R-after decisive_falsifier: dropping <skills_inline> must not shrink required."""
    import re

    from claude_bundles.cowork_skill_delivery import (
        extract_cdp_required_authority,
        parse_cdp_sealed_skill_channels,
    )

    sealed, _, _ = prepend_cdp_dispatch_skills(
        "## Task\n",
        ["path-sim", "reasoning-posture"],
    )
    mutated = re.sub(
        r"<skills_inline>.*?</skills_inline>\n?",
        "",
        sealed,
        flags=re.DOTALL,
    )
    required = extract_cdp_required_authority(mutated)
    assert required == ["path-sim", "reasoning-posture"]
    _attach, inline, _rest = parse_cdp_sealed_skill_channels(mutated)
    assert "path-sim" not in inline
    with pytest.raises(SkillDeliveryError, match="path-sim"):
        attest_delivery_channels(
            required,
            attached=["reasoning-posture"],
            inlined=inline,
        )


def test_inline_slug_shape_pin_rejects_name_attribute() -> None:
    from claude_bundles.cowork_skill_delivery import extract_inline_slugs_from_sealed

    rest = (
        '<skills_inline><skill name="path-sim" surface_class="cursor_only">'
        "body</skill></skills_inline>\n"
    )
    assert extract_inline_slugs_from_sealed(rest) == []
    rest_ok = (
        '<skills_inline><skill slug="path-sim" surface_class="cursor_only">'
        "body</skill></skills_inline>\n"
    )
    assert extract_inline_slugs_from_sealed(rest_ok) == ["path-sim"]


def test_attest_delivery_channels_mixed_and_all_inline() -> None:
    assert attest_delivery_channels(
        ["reasoning-posture", "path-sim"],
        attached=["reasoning-posture"],
        inlined=["path-sim"],
    ) == ["reasoning-posture", "path-sim"]

    assert attest_delivery_channels(
        ["path-sim"],
        attached=[],
        inlined=["path-sim"],
    ) == ["path-sim"]

    with pytest.raises(SkillDeliveryError, match="undelivered"):
        attest_delivery_channels(
            ["reasoning-posture"],
            attached=[],
            inlined=[],
        )


def test_attest_shared_sync_rejects_inline_only_channel() -> None:
    """a:27142 — shared_sync must be attached; inline alone is wrong_channel."""
    with pytest.raises(SkillDeliveryError, match="wrong_channel"):
        attest_delivery_channels(
            ["reasoning-posture"],
            attached=[],
            inlined=["reasoning-posture"],
        )


def test_cdp_inline_path_sim_is_size_gated_excerpt() -> None:
    """a:27142 — cursor_only CDP seal is excerpted, not full SKILL.md."""
    from claude_bundles.cdp_inline_excerpt import CDP_INLINE_SKILL_MAX_CHARS

    prompt, used_slash, bodies = prepend_cdp_dispatch_skills(
        "## Task\n",
        ["path-sim", "reasoning-posture"],
    )
    assert used_slash == ["reasoning-posture"]
    assert bodies[0].slug == "path-sim"
    assert len(bodies[0].body) <= CDP_INLINE_SKILL_MAX_CHARS
    assert "truncated CDP inline excerpt for path-sim" in bodies[0].body
    assert '<skill slug="path-sim"' in prompt
    full = bodies[0].path.read_text(encoding="utf-8")
    assert len(full) > CDP_INLINE_SKILL_MAX_CHARS


def test_ledger_skills_channels_row_count() -> None:
    required = ["reasoning-posture", "path-sim"]
    rows = ledger_skills_channels(
        required,
        attached=["reasoning-posture"],
        inlined=["path-sim"],
    )
    assert len(rows) == len(required)
    assert {row["delivered_via"] for row in rows} == {"attach", "inline"}


def test_no_cdp_path_references_skill_requires_code_mcp() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "claude_bundles"
    paths = [
        root / "cdp_model_endpoint_staging.py",
        root / "cowork_skill_delivery.py",
        root / "project_ask.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "skill_requires_code_mcp" not in source, path.name


def test_partition_and_prepend_cdp_dispatch_skills() -> None:
    from claude_bundles.cowork_skill_delivery import (
        format_cdp_slash_prefix,
        split_leading_slash_skills,
    )

    slash, inline = partition_cdp_skills(
        ["reasoning-posture", "claude-ai-cdp-navigation", "consult-posture"]
    )
    assert slash == ["reasoning-posture", "consult-posture"]
    assert inline == ["claude-ai-cdp-navigation"]
    assert format_cdp_slash_prefix(slash) == (
        "/reasoning-posture\n"
        "/consult-posture\n"
    )
    prompt, used_slash, bodies = prepend_cdp_dispatch_skills(
        "## Task\ndo the thing\n",
        ["reasoning-posture", "claude-ai-cdp-navigation"],
    )
    assert used_slash == ["reasoning-posture"]
    assert bodies[0].slug == "claude-ai-cdp-navigation"
    assert prompt.startswith("/reasoning-posture\n\n<skills_inline>")
    assert '<skill slug="claude-ai-cdp-navigation"' in prompt
    assert "</skills_inline>" in prompt
    assert "## Task" in prompt
    tokens, rest = split_leading_slash_skills(
        "/reasoning-posture\n/consult-posture\n\nBODY"
    )
    assert tokens == ["/reasoning-posture", "/consult-posture"]
    assert rest == "\nBODY"


def test_split_leading_slash_skills_preserves_blank_line_before_body() -> None:
    from claude_bundles.cowork_skill_delivery import split_leading_slash_skills

    tokens, rest = split_leading_slash_skills(
        "/path-sim\n/cdp-operator-proxy\n\n# Sealed\n"
    )
    assert tokens == ["/path-sim", "/cdp-operator-proxy"]
    assert rest == "\n# Sealed\n"


def test_multi_slash_prefix_two_shared_sync_slugs() -> None:
    from claude_bundles.cowork_skill_delivery import (
        format_cdp_slash_prefix,
        prepend_cdp_dispatch_skills,
    )

    slugs = ["reasoning-posture", "consult-posture"]
    assert format_cdp_slash_prefix(slugs) == (
        "/reasoning-posture\n/consult-posture\n"
    )
    prompt, used_slash, bodies = prepend_cdp_dispatch_skills("## Task\n", slugs)
    assert used_slash == slugs
    assert bodies == []
    assert prompt == (
        "/reasoning-posture\n"
        "/consult-posture\n"
        "\n"
        "<!--cdp-required-skills:reasoning-posture,consult-posture-->\n"
        "## Task\n"
    )
    assert "Use the `" not in prompt


def test_hybrid_escape_prefix_still_available() -> None:
    from claude_bundles.cowork_skill_delivery import (
        format_cdp_hybrid_prefix,
        prepend_cdp_dispatch_skills,
    )

    slugs = ["reasoning-posture", "consult-posture"]
    assert format_cdp_hybrid_prefix(slugs) == (
        "/reasoning-posture\nUse the `consult-posture` skill\n"
    )
    prompt, used_slash, bodies = prepend_cdp_dispatch_skills(
        "## Task\n", slugs, hybrid_escape=True
    )
    assert used_slash == slugs
    assert bodies == []
    assert prompt == (
        "/reasoning-posture\n"
        "Use the `consult-posture` skill\n"
        "\n"
        "<!--cdp-required-skills:reasoning-posture,consult-posture-->\n"
        "## Task\n"
    )


def test_single_shared_sync_slash_chip_unchanged() -> None:
    from claude_bundles.cowork_skill_delivery import (
        format_cdp_slash_prefix,
        prepend_cdp_dispatch_skills,
    )

    assert format_cdp_slash_prefix(["reasoning-posture"]) == "/reasoning-posture\n"
    prompt, used_slash, bodies = prepend_cdp_dispatch_skills(
        "## Task\n",
        ["reasoning-posture"],
    )
    assert used_slash == ["reasoning-posture"]
    assert bodies == []
    assert prompt.startswith(
        "/reasoning-posture\n\n<!--cdp-required-skills:reasoning-posture-->\n## Task\n"
    )
    assert "Use the `" not in prompt


def test_format_cdp_slash_prefix_fail_closed_when_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_bundles.cowork_skill_delivery as delivery
    from claude_bundles.cowork_skill_delivery import format_cdp_slash_prefix

    monkeypatch.setattr(delivery, "MULTI_CHIP_PROVEN", False)
    with pytest.raises(SkillDeliveryError, match="refusing consecutive multi-slash"):
        format_cdp_slash_prefix(["reasoning-posture", "consult-posture"])


def test_blank_line_before_skills_inline_with_multi_slash() -> None:
    from claude_bundles.cowork_skill_delivery import prepend_cdp_dispatch_skills

    prompt, _, _ = prepend_cdp_dispatch_skills(
        "## Task\n",
        ["reasoning-posture", "consult-posture", "claude-ai-cdp-navigation"],
    )
    marker = "/consult-posture\n\n<skills_inline>"
    assert marker in prompt
    assert prompt.startswith("/reasoning-posture\n/consult-posture\n")
    assert "Use the `" not in prompt.split("<skills_inline>", 1)[0]


def test_chip_attest_fails_when_empty() -> None:
    with pytest.raises(SkillDeliveryError, match="skills.enabled is empty"):
        attest_skills_chip_enabled([], required=["operator-posture"])


def test_chip_attest_passes_when_nonempty() -> None:
    assert attest_skills_chip_enabled(
        ["operator-posture"], required=["operator-posture"]
    ) == ["operator-posture"]


def test_inject_attest_fails_on_missing() -> None:
    with pytest.raises(SkillDeliveryError, match="injected skills missing"):
        attest_injected_slugs(["git-posture"], required=["operator-posture", "git-posture"])


def test_inject_roundtrip_prepends_bodies() -> None:
    slugs = ["claude-ai-cdp-navigation"]
    bodies = load_skill_bodies(slugs)
    assert len(bodies) == 1
    block = render_injected_skills_block(bodies)
    assert "NOT GitHub" in block
    assert "skill:claude-ai-cdp-navigation" in block
    prompt, used = prepend_injected_skills("## Task\n\ndo the thing\n", slugs)
    assert used[0].slug == "claude-ai-cdp-navigation"
    assert prompt.startswith("# Injected skill bodies")
    assert "## Task" in prompt


def test_ledger_ok_requires_delivery() -> None:
    empty = ledger_skills_record(enabled=[], injected=[], required=[])
    assert empty["ok"] is False
    assert "gitignored" in empty["note"]
    ok = ledger_skills_record(
        enabled=["reasoning-posture"],
        injected=["path-sim"],
        required=["reasoning-posture", "path-sim"],
    )
    assert ok["ok"] is True
    assert len(ok["rows"]) == 2
    assert "gitignored" in github_cannot_load_skill_trees_note()
