"""Tests for unified skills= merge and channel partition."""

from __future__ import annotations

import pytest
from implement_admission.skill_catalog_resolver import canonical_agent_skill_id
from skills_mount.anthropic_container import (
    AnthropicContainerSkillError,
    clear_anthropic_skills_index_cache_for_tests,
)

from agent_seat.skills_merge import (
    EffectiveSkill,
    McpPredicatedSkillsRejectedError,
    enforce_mcp_predicated_skills,
    enrich_rows_with_inline_drops,
    partition_skill_channels,
    resolve_effective_skills,
)


def test_resolve_effective_skills_dedupes_caller_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_seat.inject_registry.scope_default_skill_ids",
        lambda *_a, **_k: (),
    )
    effective = resolve_effective_skills(
        ["architecture-invariants", "architecture-invariants"],
        role=None,
        platform="*",
        inject_profile=None,
        code_touching=False,
        packet_invariant_ids=(),
    )
    assert len(effective) == 1
    assert effective[0].origin == "caller"
    assert effective[0].canonical_id == canonical_agent_skill_id(
        "architecture-invariants"
    )


def test_resolve_effective_skills_caller_wins_over_scope_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_seat.inject_registry.scope_default_skill_ids",
        lambda *_a, **_k: ("rule:architecture-invariants",),
    )
    effective = resolve_effective_skills(
        ["architecture-invariants"],
        role="reviewer",
        platform="api",
        inject_profile="dispatch",
        code_touching=True,
        packet_invariant_ids=(),
    )
    assert len(effective) == 1
    assert effective[0].origin == "caller"


def test_partition_openai_mounts_entire_effective_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_seat.skills_merge._read_mount_backend",
        lambda _model: "openai_container",
    )
    effective = (
        EffectiveSkill(
            requested_id="agent-identity-signoff",
            canonical_id=canonical_agent_skill_id("agent-identity-signoff"),
            origin="caller",
        ),
    )
    partition = partition_skill_channels(
        effective,
        model="openai/gpt-5.5",
        mcp_enabled=True,
        role="reviewer",
        platform="api",
        inject_profile="dispatch",
        code_touching=True,
    )
    assert partition.layer_b == ("agent-identity-signoff",)
    assert partition.layer_a == ()
    assert partition.layer_c == ()
    assert partition.rows[0].channel == "layer_b"


def test_partition_anthropic_container_mounts_mapped_up_to_eight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import json

    index_path = tmp_path / "anthropic_skills_index.json"
    skills = {
        f"skill-{i}": {"skill_id": f"sk_{i}", "type": "custom", "version": "latest"}
        for i in range(10)
    }
    index_path.write_text(json.dumps({"skills": skills}), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_SKILLS_INDEX_PATH", str(index_path))
    clear_anthropic_skills_index_cache_for_tests()
    monkeypatch.setattr(
        "agent_seat.skills_merge._read_mount_backend",
        lambda _model: "anthropic_container",
    )
    effective = tuple(
        EffectiveSkill(
            requested_id=f"skill-{i}",
            canonical_id=canonical_agent_skill_id(f"skill-{i}"),
            origin="caller",
        )
        for i in range(10)
    )
    partition = partition_skill_channels(
        effective,
        model="anthropic/claude-sonnet-5-5",
        mcp_enabled=True,
        role="synthesizer",
        platform="api",
        inject_profile="dispatch",
        code_touching=True,
    )
    assert len(partition.layer_b) == 8
    assert len(partition.layer_c) == 2
    clear_anthropic_skills_index_cache_for_tests()


def test_partition_anthropic_unmapped_in_first_eight_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import json

    index_path = tmp_path / "anthropic_skills_index.json"
    index_path.write_text(
        json.dumps({"skills": {"mapped-skill": {"skill_id": "sk_1", "type": "custom"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_SKILLS_INDEX_PATH", str(index_path))
    clear_anthropic_skills_index_cache_for_tests()
    monkeypatch.setattr(
        "agent_seat.skills_merge._read_mount_backend",
        lambda _model: "anthropic_container",
    )
    effective = (
        EffectiveSkill(
            requested_id="missing-skill",
            canonical_id=canonical_agent_skill_id("missing-skill"),
            origin="caller",
        ),
    )
    with pytest.raises(AnthropicContainerSkillError, match="not indexed"):
        partition_skill_channels(
            effective,
            model="anthropic/claude-sonnet-5-5",
            mcp_enabled=True,
            role="synthesizer",
            platform="api",
            inject_profile="dispatch",
            code_touching=True,
        )
    clear_anthropic_skills_index_cache_for_tests()


def test_partition_anthropic_routes_caller_to_layer_c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_seat.skills_merge._read_mount_backend",
        lambda _model: "none",
    )
    effective = (
        EffectiveSkill(
            requested_id="engagement-stance",
            canonical_id=canonical_agent_skill_id("engagement-stance"),
            origin="caller",
        ),
    )
    partition = partition_skill_channels(
        effective,
        model="anthropic/claude-opus-4-8",
        mcp_enabled=True,
        role="reviewer",
        platform="api",
        inject_profile=None,
        code_touching=False,
    )
    assert partition.layer_a == ()
    assert partition.layer_c == ("engagement-stance",)
    assert partition.rows[0].channel == "layer_c"


def test_partition_inline_only_routes_to_layer_c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_seat.skills_merge._read_mount_backend",
        lambda _model: "none",
    )
    effective = (
        EffectiveSkill(
            requested_id="engagement-stance",
            canonical_id=canonical_agent_skill_id("engagement-stance"),
            origin="caller",
        ),
    )
    partition = partition_skill_channels(
        effective,
        model="xai/grok-3",
        mcp_enabled=False,
        role="skeptic",
        platform="api",
        inject_profile=None,
        code_touching=False,
    )
    assert partition.layer_c == ("engagement-stance",)
    assert partition.rows[0].channel == "layer_c"


def test_enrich_rows_marks_provider_mounted_drop() -> None:
    from agent_seat.skills_merge import SkillChannelRow

    channel_rows = (
        SkillChannelRow(
            requested_id="architecture-invariants",
            canonical_id=canonical_agent_skill_id("architecture-invariants"),
            origin="scope_default",
            channel="layer_c",
            disposition="delivered",
        ),
    )
    enriched = enrich_rows_with_inline_drops(
        channel_rows,
        [{"id": "rule:architecture-invariants", "reason": "provider_mounted"}],
    )
    assert enriched[0].disposition == "dropped"
    assert enriched[0].drop_reason == "provider_mounted"


def test_enforce_mcp_predicated_identity_when_mcp_enabled() -> None:
    effective = (
        EffectiveSkill(
            requested_id="cortex",
            canonical_id=canonical_agent_skill_id("cortex"),
            origin="caller",
        ),
    )
    filtered, skip_rows = enforce_mcp_predicated_skills(effective, mcp_enabled=True)
    assert filtered == effective
    assert skip_rows == ()


def test_enforce_mcp_predicated_rejects_all_caller_offenders() -> None:
    effective = (
        EffectiveSkill(
            requested_id="cortex",
            canonical_id=canonical_agent_skill_id("cortex"),
            origin="caller",
        ),
        EffectiveSkill(
            requested_id="fs",
            canonical_id=canonical_agent_skill_id("fs"),
            origin="caller",
        ),
    )
    with pytest.raises(McpPredicatedSkillsRejectedError) as exc_info:
        enforce_mcp_predicated_skills(effective, mcp_enabled=False)
    assert set(exc_info.value.skills) == {"cortex", "fs"}


def test_enforce_mcp_predicated_skips_scope_defaults() -> None:
    effective = (
        EffectiveSkill(
            requested_id="cortex-orientation",
            canonical_id=canonical_agent_skill_id("cortex-orientation"),
            origin="scope_default",
        ),
        EffectiveSkill(
            requested_id="architecture-invariants",
            canonical_id=canonical_agent_skill_id("architecture-invariants"),
            origin="scope_default",
        ),
    )
    filtered, skip_rows = enforce_mcp_predicated_skills(effective, mcp_enabled=False)
    assert len(filtered) == 1
    assert filtered[0].requested_id == "architecture-invariants"
    assert len(skip_rows) == 1
    assert skip_rows[0].channel == "none"
    assert skip_rows[0].drop_reason == "mcp_predicated_skip"
    assert skip_rows[0].requested_id == "cortex-orientation"


def test_enforce_mcp_predicated_mixed_origin_matrix() -> None:
    effective = (
        EffectiveSkill(
            requested_id="cortex-orientation",
            canonical_id=canonical_agent_skill_id("cortex-orientation"),
            origin="scope_default",
        ),
        EffectiveSkill(
            requested_id="cortex",
            canonical_id=canonical_agent_skill_id("cortex"),
            origin="caller",
        ),
        EffectiveSkill(
            requested_id="architecture-invariants",
            canonical_id=canonical_agent_skill_id("architecture-invariants"),
            origin="caller",
        ),
    )
    with pytest.raises(McpPredicatedSkillsRejectedError) as exc_info:
        enforce_mcp_predicated_skills(effective, mcp_enabled=False)
    assert exc_info.value.skills == ("cortex",)


def test_enforce_mcp_predicated_classification_missing_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from implement_admission.skill_mcp_classification import (
        SkillClassificationMissingError,
    )

    def _missing(_slug: str) -> bool:
        raise SkillClassificationMissingError("canonical slug 'unknown-skill' absent")

    monkeypatch.setattr(
        "implement_admission.skill_mcp_classification.skill_mcp_predicated",
        _missing,
    )
    effective = (
        EffectiveSkill(
            requested_id="unknown-skill",
            canonical_id="unknown-skill",
            origin="caller",
        ),
    )
    with pytest.raises(SkillClassificationMissingError):
        enforce_mcp_predicated_skills(effective, mcp_enabled=False)
