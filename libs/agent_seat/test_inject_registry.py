"""Tests for the unified scoped inject registry."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from agent_seat.body_injection import (
    RequiredBodyUnresolved,
    clear_payload_cache_for_tests,
)
from agent_seat.inject_registry import (
    SENTINEL_DISPATCH_INJECT_ENTITY_ID,
    InjectScope,
    active_scopes,
    assert_boot_session_gate_complete,
    assert_must_inline_allowlist_valid,
    injected_skill_slugs,
    parse_packet_invariant_skill_ids,
    resolve_injected_bodies,
)
from agent_seat.prompts import assemble_system_prompt

_MCP_SERVER_ROOT = Path(__file__).resolve().parents[2] / "services" / "mcp-server"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_payload_cache_for_tests()


def _body_map(monkeypatch: pytest.MonkeyPatch, bodies: dict[str, str]) -> None:
    expanded: dict[str, str] = dict(bodies)
    for key, value in list(bodies.items()):
        if key.startswith("agent_skill:"):
            slug = key.split(":", 1)[1]
            expanded.setdefault(f"rule:{slug}", value)
        elif key.startswith("rule:"):
            slug = key.split(":", 1)[1]
            expanded.setdefault(f"agent_skill:{slug}", value)
    # Registry ulg entry uses bare slug.
    if "agent_skill:ulg-architecture" in expanded:
        expanded.setdefault(
            "rule:ulg-architecture", expanded["agent_skill:ulg-architecture"]
        )

    def fake_resolve(
        slug_or_entity_id: str,
        *,
        include_non_active: bool = False,
        expected_digest: str | None = None,
        conn: object | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        del conn, expected_digest
        lookup = slug_or_entity_id
        if lookup not in expanded and ":" in lookup:
            lookup = lookup.split(":", 1)[1]
        if lookup not in expanded:
            prefixed = f"agent_skill:{lookup}"
            if prefixed in expanded:
                lookup = prefixed
            elif f"rule:{lookup}" in expanded:
                lookup = f"rule:{lookup}"
        if lookup not in expanded:
            return None, "body_missing"
        if lookup == "rule:orchestrator-core" and not include_non_active:
            return None, "body_missing"
        slug = lookup.split(":", 1)[-1]
        digest = f"sha256:{slug}"
        return {"digest": digest, "body": expanded[lookup], "id": lookup}, None

    monkeypatch.setattr(
        "implement_admission.skill_body_resolve.resolve_skill_body_from_table",
        fake_resolve,
    )


def test_must_inline_regression_guard() -> None:
    assert_must_inline_allowlist_valid()


def test_boot_session_gate_completeness_api() -> None:
    assert_boot_session_gate_complete(platform="api")


def test_boot_session_gate_completeness_web() -> None:
    assert_boot_session_gate_complete(platform="web")


def test_parse_packet_invariant_skill_ids_name_only_and_legacy() -> None:
    """Name-only refs parse; legacy fs path still recognized for old packets."""
    name_only = """
<invariants>
- agent_skill:sentinel-dispatch-inject-19887
- Use the `architecture-invariants` skill
</invariants>
"""
    assert parse_packet_invariant_skill_ids(name_only) == (
        "agent_skill:sentinel-dispatch-inject-19887",
        "agent_skill:architecture-invariants",
    )
    legacy = """
<invariants>
- fs(cortex, md_read, agent-skills/architecture-invariants.md)
</invariants>
"""
    assert parse_packet_invariant_skill_ids(legacy) == (
        "agent_skill:architecture-invariants",
    )


def test_active_scopes_lead_dispatch_union() -> None:
    scopes = active_scopes("claude-web", "dispatch", platform="web")
    assert scopes == {InjectScope.DISPATCH_PACKET}


def test_active_scopes_non_web_dispatch_includes_universal() -> None:
    scopes = active_scopes("claude-api", "dispatch", platform="api")
    assert scopes == {
        InjectScope.UNIVERSAL,
        InjectScope.DISPATCH_PACKET,
    }


def test_active_scopes_web_standard_empty() -> None:
    assert active_scopes("claude-web", None, platform="web") == set()


def test_api_boot_injects_universal_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "orientation-body",
        "agent_skill:cortex-provenance-discipline": "provenance",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        budget_bytes=None,
    )
    injected_ids = [item["id"] for item in resolution.injected]
    assert "rule:cortex-orientation" in injected_ids
    assert "orientation-body" in resolution.block_md


def test_web_boot_skips_static_registry_inject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tier_marker = "MODEL_TIER_AWARENESS_WEB_MUST_INLINE_MARKER"
    bodies = {
        "agent_skill:cortex-orientation": "orientation",
        "agent_skill:cortex-provenance-discipline": "provenance",
        "agent_skill:model-tier-awareness-web": tier_marker,
        "agent_skill:orchestrator-core": "orch",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-web",
        role="claude-web",
        platform="web",
        budget_bytes=None,
    )
    assert resolution.injected == []
    assert resolution.block_md == ""


def test_union_matrix_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "orientation",
        "agent_skill:cortex-provenance-discipline": "provenance",
        "agent_skill:model-tier-awareness-web": "tier-web",
        "agent_skill:orchestrator-core": "orchestrator-core",
        "agent_skill:orchestrator-workflow": "orchestrator-workflow",
        "agent_skill:architecture-invariants": "arch",
        "agent_skill:ulg-architecture": "ulg",
        "agent_skill:sentinel-dispatch-inject-19887": "sentinel-body",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-web",
        role="claude-web",
        platform="web",
        inject_profile="dispatch",
        code_touching=True,
        packet_invariant_ids=(SENTINEL_DISPATCH_INJECT_ENTITY_ID,),
        budget_bytes=None,
    )
    injected_ids = [item["id"] for item in resolution.injected]
    assert len(injected_ids) == len(set(injected_ids))
    assert resolution.telemetry["dedupe_collisions"] == 0
    assert set(injected_ids) == {
        "rule:orchestrator-workflow",
        "rule:architecture-invariants",
        "rule:ulg-architecture",
        SENTINEL_DISPATCH_INJECT_ENTITY_ID,
    }


def test_injected_skill_slugs_matches_resolver_filter() -> None:
    slugs = injected_skill_slugs(
        role="claude-web",
        platform="web",
        inject_profile="dispatch",
        code_touching=True,
        packet_invariant_ids=(SENTINEL_DISPATCH_INJECT_ENTITY_ID,),
    )
    assert "sentinel-dispatch-inject-19887" in slugs
    assert "architecture-invariants" in slugs


_FORMER_CHANNEL_ACCOUNTING_SLUGS = (
    "operator-posture",
    "lead-seat-boot",
    "consult-routing",
    "dispatch-shape",
    "git-posture",
    "entity-lifecycle-discipline",
    "session-close",
    "session-close-audit",
    "web-transcript-preprocessing",
    "frontier-reasoning-discipline",
    "prose-discipline",
)


def test_loaded_set_no_longer_suppresses_channel_accounting_slugs() -> None:
    """Regression: LOADED_SET registry entries for inject channels were removed."""
    slugs = injected_skill_slugs(
        role="lead",
        platform="web",
        include_loaded_set=True,
    )
    for slug in _FORMER_CHANNEL_ACCOUNTING_SLUGS:
        assert slug not in slugs


def test_fail_closed_critical_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "x" * 60_000,
        "agent_skill:cortex-provenance-discipline": "small",
    }
    _body_map(monkeypatch, bodies)
    with pytest.raises(RequiredBodyUnresolved):
        resolve_injected_bodies(
            "claude-api",
            platform="api",
            budget_bytes=1000,
        )


def test_must_inline_emits_fail_closed_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "critical-small",
        "agent_skill:cortex-provenance-discipline": "y" * 4000,
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        budget_bytes=500,
    )
    assert "inject:FAIL_CLOSED" in resolution.block_md
    assert resolution.telemetry.get("fail_closed_reason") == "must_inline_budget"


def test_normal_overflow_degrades_to_index(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "c",
        "agent_skill:architecture-invariants": "a" * 200,
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "gatherer",
        role="gatherer",
        platform="api",
        code_touching=True,
        budget_bytes=120,
    )
    assert "injected-index:architecture-invariants" in resolution.block_md
    assert any(d.get("reason") == "budget_index" for d in resolution.dropped)


def test_lifecycle_required_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_resolve(
        slug_or_entity_id: str,
        *,
        include_non_active: bool = False,
        expected_digest: str | None = None,
        conn: object | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        del expected_digest, conn
        if slug_or_entity_id == "orchestrator-core" and not include_non_active:
            return (
                {
                    "id": "rule:orchestrator-core",
                    "body": None,
                    "digest": "sha256:orch",
                    "reason": "inactive_lifecycle_withheld",
                },
                None,
            )
        if slug_or_entity_id == "cortex-orientation":
            return {"digest": "sha256:o", "body": "orientation"}, None
        if slug_or_entity_id == "cortex-provenance-discipline":
            return {"digest": "sha256:p", "body": "provenance"}, None
        return None, "body_missing"

    monkeypatch.setattr(
        "implement_admission.skill_body_resolve.resolve_skill_body_from_table",
        fake_resolve,
    )
    monkeypatch.setattr(
        "agent_seat.inject_registry.is_lead_agent",
        lambda slug: slug == "claude-api",
    )
    resolution = resolve_injected_bodies(
        "claude-api",
        role="claude-api",
        platform="api",
        budget_bytes=None,
    )
    assert any(
        d.get("reason") == "inactive_lifecycle_withheld" for d in resolution.dropped
    )


def _stub_boot_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if str(_MCP_SERVER_ROOT) not in sys.path:
        sys.path.insert(0, str(_MCP_SERVER_ROOT))
    from tools.cortex_named_tools import _boot_audit_dump as boot_audit_dump
    from tools.cortex_named_tools import _boot_runner as boot_runner

    monkeypatch.setattr(boot_runner, "resolve_transcript", lambda _tid: None)
    monkeypatch.setattr(
        boot_runner,
        "build_futures_spec",
        lambda _agent, _profile, _recorder: {"placeholder": (lambda: {},)},
    )
    monkeypatch.setattr(
        boot_runner,
        "extract_boot_results",
        lambda _agent, _raw, _profile: {
            "sessions": [],
            "deadlines": [],
            "threads": [],
            "unread_turns": [],
            "staging_items": [],
            "todos": [],
            "self_reflections": [],
            "rj_entries": [],
            "rj_total": 0,
            "recent_mentions": [],
            "skills": [],
            "plan_phases": [],
            "in_flight_todos": [],
            "temporal_active": [],
            "expired_unresolved": [],
            "review_total": 0,
            "rag_pipeline": {},
            "audit_counters": None,
            "async_dispatches": [],
            "rules": [],
            "open_arcs": [],
            "skills_card_markdown": None,
            "skills_concise_markdown": None,
            "skills_unpartitioned_count": 0,
            "continuity": None,
            "principal_context": None,
        },
    )
    monkeypatch.setattr(boot_runner, "build_unread_threads", lambda _threads: [])
    monkeypatch.setattr(boot_runner, "build_review_top", lambda _items: [])
    monkeypatch.setattr(
        boot_runner,
        "render_operational_context",
        lambda **_kwargs: "operational context",
    )
    monkeypatch.setattr(
        boot_runner,
        "render_briefing_card",
        lambda **_kwargs: ("briefing card", []),
    )
    monkeypatch.setattr(boot_runner, "record", lambda signal, **_kw: None)
    monkeypatch.setattr(boot_runner, "write_audit_dump", lambda **_kw: None)
    monkeypatch.setattr(boot_runner, "_materialize_views", lambda _views: [])
    monkeypatch.setattr(boot_runner, "_list_files", lambda _path: {"files": []})
    monkeypatch.setattr(
        boot_runner,
        "_materialize_skills_index",
        lambda *_args, **_kwargs: ("", "", False),
    )

    shared_dir = tmp_path / "shared"
    shared_dir.mkdir(parents=True)
    monkeypatch.setattr(boot_runner, "_OPS_CONTEXT_DIR", shared_dir)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(boot_audit_dump, "AUDIT_DIR", audit_dir)


@pytest.mark.asyncio
async def test_sentinel_in_dispatch_boot_and_hydrate_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sentinel_body = "SENTINEL_DISPATCH_INJECT_19887_BODY_UNIQUE_MARKER"
    bodies = {
        "agent_skill:cortex-orientation": "orientation",
        "agent_skill:cortex-provenance-discipline": "provenance",
        SENTINEL_DISPATCH_INJECT_ENTITY_ID: sentinel_body,
    }
    _body_map(monkeypatch, bodies)
    packet = f"<invariants>\n- {SENTINEL_DISPATCH_INJECT_ENTITY_ID}\n</invariants>"
    packet_ids = parse_packet_invariant_skill_ids(packet)

    _stub_boot_runner(monkeypatch, tmp_path)
    from tools.cortex_named_tools._boot_runner import BootMode, run_cortex_boot

    boot_result = run_cortex_boot(
        family="claude",
        platform="web",
        mode=BootMode.INSPECT,
        profile="dispatch",
        packet_text=packet,
    )
    resolution = resolve_injected_bodies(
        "claude-web",
        role="claude-web",
        platform="web",
        inject_profile="dispatch",
        packet_invariant_ids=packet_ids,
        budget_bytes=None,
    )
    assert sentinel_body in resolution.block_md
    injected_artifacts = boot_result.get("injected_artifacts") or []
    assert any(a.get("name") == "auto_inject_skills" for a in injected_artifacts)

    from agent_seat import hydration as _hyd

    class _EmptyScripted:
        async def __call__(self, path: str) -> Any:
            del path
            return {}

    monkeypatch.setattr(_hyd, "_cortex_get", _EmptyScripted())
    monkeypatch.setattr(_hyd, "_bus_get", _EmptyScripted())

    bundle = await _hyd.hydrate_agent(
        "gatherer",
        model="openai/gpt-5.5",
        inject_profile="dispatch",
        packet_invariant_ids=packet_ids,
    )
    hydrate_system = assemble_system_prompt(
        "gatherer",
        briefing_card_md=bundle.briefing_card_md,
        continuation_md=bundle.continuation_md,
        injected_bodies_md=bundle.injected_bodies_md,
        inline_only=bundle.inline_only,
    )
    assert sentinel_body in hydrate_system, (
        "sentinel missing from hydrate_agent generate-path assembled system string"
    )


@pytest.mark.asyncio
async def test_lead_web_boot_skips_static_auto_inject(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Standard web lead boot: skills operator-attached in claude.ai UI."""
    orch_marker = "ORCHESTRATOR_CORE_LEAD_INJECT_MARKER"
    tier_marker = "MODEL_TIER_AWARENESS_WEB_LEAD_BOOT_MARKER"
    bodies = {
        "agent_skill:cortex-orientation": "orientation",
        "agent_skill:cortex-provenance-discipline": "provenance",
        "agent_skill:model-tier-awareness-web": tier_marker,
        "agent_skill:orchestrator-core": orch_marker,
    }
    _body_map(monkeypatch, bodies)
    monkeypatch.setattr(
        "agent_seat.inject_registry.is_lead_agent",
        lambda slug: slug == "claude-web",
    )

    _stub_boot_runner(monkeypatch, tmp_path)
    from tools.cortex_named_tools._boot_runner import BootMode, run_cortex_boot

    boot_result = run_cortex_boot(
        family="claude",
        platform="web",
        role="lead",
        mode=BootMode.INSPECT,
    )
    assert "auto_inject_skills_ref" not in boot_result
    assert "auto_inject_skills_md" not in boot_result
    assert not any(
        a.get("name") == "auto_inject_skills"
        for a in boot_result.get("injected_artifacts") or []
    )


def test_provider_mount_excludes_matching_canonical_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:architecture-invariants": "arch body",
        "agent_skill:cortex-orientation": "orientation",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        inject_profile="dispatch",
        code_touching=True,
        provider_mount_slugs=frozenset({"architecture-invariants"}),
        budget_bytes=None,
    )
    injected_ids = {str(item.get("id") or "") for item in resolution.injected}
    assert "rule:architecture-invariants" not in injected_ids
    assert any(
        item.get("reason") == "provider_mounted"
        and item.get("id") == "rule:architecture-invariants"
        for item in resolution.dropped
    )


def test_provider_mount_excludes_alias_input_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:ulg-architecture": "ulg body",
        "agent_skill:cortex-orientation": "orientation",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        inject_profile="dispatch",
        code_touching=True,
        provider_mount_slugs=frozenset({"ulg-architecture"}),
        budget_bytes=None,
    )
    injected_ids = {str(item.get("id") or "") for item in resolution.injected}
    assert "rule:ulg-architecture" not in injected_ids
    assert any(item.get("reason") == "provider_mounted" for item in resolution.dropped)


def test_caller_skill_ids_resolve_as_mandatory_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:architecture-invariants": "arch body",
        "agent_skill:cortex-orientation": "orientation",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        inject_profile="dispatch",
        code_touching=True,
        caller_skill_ids=("architecture-invariants",),
        budget_bytes=None,
    )
    injected_ids = {str(item.get("id") or "") for item in resolution.injected}
    assert "agent_skill:architecture-invariants" in injected_ids


def test_caller_skill_mandatory_raises_on_budget_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:architecture-invariants": "x" * 60_000,
        "agent_skill:cortex-orientation": "small",
    }
    _body_map(monkeypatch, bodies)
    with pytest.raises(RequiredBodyUnresolved) as exc_info:
        resolve_injected_bodies(
            "claude-api",
            platform="api",
            inject_profile="dispatch",
            caller_skill_ids=("architecture-invariants",),
            budget_bytes=1000,
        )
    dropped = exc_info.value.dropped
    assert any(item.get("reason") == "layer_c_budget" for item in dropped)
    assert any(item.get("slug") == "architecture-invariants" for item in dropped)


def test_caller_mcp_predicated_stripped_from_mandatory_no_budget_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "x" * 60_000,
        "agent_skill:cortex-provenance-discipline": "small",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        caller_skill_ids=("cortex-orientation",),
        budget_bytes=1000,
        exclude_mcp_predicated=True,
    )
    assert any(item.get("reason") == "mcp_predicated_skip" for item in resolution.dropped)
    assert "x" * 100 not in resolution.block_md


def test_caller_skill_unresolvable_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_seat.inject_registry import CallerSkillUnresolvedError

    with pytest.raises(CallerSkillUnresolvedError, match="definitely-not-a-skill"):
        resolve_injected_bodies(
            "claude-api",
            caller_skill_ids=("definitely-not-a-skill",),
            budget_bytes=None,
        )


def test_merged_overlap_provider_mounted_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:architecture-invariants": "arch body",
        "agent_skill:cortex-orientation": "orientation",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        inject_profile="dispatch",
        code_touching=True,
        caller_skill_ids=("architecture-invariants",),
        provider_mount_slugs=frozenset({"architecture-invariants"}),
        budget_bytes=None,
    )
    injected_ids = {str(item.get("id") or "") for item in resolution.injected}
    assert "agent_skill:architecture-invariants" not in injected_ids
    assert any(
        item.get("reason") == "provider_mounted"
        for item in resolution.dropped
    )


def test_exclude_mcp_predicated_drops_predicated_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "orientation-body",
        "agent_skill:cortex-provenance-discipline": "provenance-body",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        budget_bytes=None,
        exclude_mcp_predicated=True,
    )
    dropped_reasons = {
        item["id"]: item["reason"] for item in resolution.dropped
    }
    assert dropped_reasons.get("rule:cortex-orientation") == "mcp_predicated_skip"
    assert "orientation-body" not in resolution.block_md
    assert "provenance-body" in resolution.block_md


def test_exclude_mcp_predicated_critical_tier_no_required_body_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "orientation-body",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        budget_bytes=None,
        exclude_mcp_predicated=True,
    )
    assert resolution.injected == []
    assert any(
        item.get("reason") == "mcp_predicated_skip"
        for item in resolution.dropped
    )


def test_exclude_mcp_predicated_mandatory_bundle_predicated_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "orientation",
        "agent_skill:cortex-provenance-discipline": "provenance",
        "agent_skill:orchestrator-workflow": "orchestrator-workflow",
        "agent_skill:architecture-invariants": "arch",
        "agent_skill:ulg-architecture": "ulg",
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-web",
        role="claude-web",
        platform="web",
        inject_profile="dispatch",
        code_touching=True,
        budget_bytes=None,
        inline_only_dispatch=True,
        exclude_mcp_predicated=True,
    )
    injected_ids = {item["id"] for item in resolution.injected}
    assert "rule:orchestrator-workflow" not in injected_ids
    assert "rule:architecture-invariants" in injected_ids
    assert any(
        item.get("id") == "rule:orchestrator-workflow"
        and item.get("reason") == "mcp_predicated_skip"
        for item in resolution.dropped
    )


def test_exclude_mcp_predicated_false_preserves_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "orientation-body",
        "agent_skill:cortex-provenance-discipline": "provenance-body",
    }
    _body_map(monkeypatch, bodies)
    baseline = resolve_injected_bodies(
        "claude-api",
        platform="api",
        budget_bytes=None,
    )
    with_flag = resolve_injected_bodies(
        "claude-api",
        platform="api",
        budget_bytes=None,
        exclude_mcp_predicated=False,
    )
    assert with_flag.block_md == baseline.block_md
    assert with_flag.injected == baseline.injected
    assert with_flag.dropped == baseline.dropped


def test_caller_ulg_suffix_no_longer_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_seat.inject_registry import CallerSkillUnresolvedError

    shared_body = "ULG_ARCHITECTURE_SHARED_BODY_MARKER"
    _body_map(
        monkeypatch,
        {
            "agent_skill:ulg-architecture": shared_body,
            "agent_skill:cortex-orientation": "orientation",
        },
    )
    bare = resolve_injected_bodies(
        "claude-api",
        platform="api",
        inject_profile="dispatch",
        caller_skill_ids=("ulg-architecture",),
        budget_bytes=None,
    )
    assert shared_body in bare.block_md
    with pytest.raises(CallerSkillUnresolvedError, match="ulg-architecture_ulg"):
        resolve_injected_bodies(
            "claude-api",
            platform="api",
            inject_profile="dispatch",
            caller_skill_ids=("ulg-architecture_ulg",),
            budget_bytes=None,
        )


def test_injected_markers_carry_resolver_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_digest = "sha256:table-digest-from-resolver"

    def fake_resolve(
        slug_or_entity_id: str,
        *,
        include_non_active: bool = False,
        expected_digest: str | None = None,
        conn: object | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        del include_non_active, expected_digest, conn
        slug = slug_or_entity_id.split(":", 1)[-1]
        if slug == "architecture-invariants":
            return {
                "id": "agent_skill:architecture-invariants",
                "digest": table_digest,
                "body": "arch body",
            }, None
        if slug == "cortex-orientation":
            return {
                "id": "rule:cortex-orientation",
                "digest": "sha256:cortex-orientation",
                "body": "orientation",
            }, None
        return None, "body_missing"

    monkeypatch.setattr(
        "implement_admission.skill_body_resolve.resolve_skill_body_from_table",
        fake_resolve,
    )
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        inject_profile="dispatch",
        caller_skill_ids=("architecture-invariants",),
        budget_bytes=None,
    )
    assert f"digest:{table_digest}" in resolution.block_md
    assert any(item["digest"] == table_digest for item in resolution.injected)


def test_phantom_agent_skill_id_resolves_via_table_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller entity id absent from DB still inline-resolves via table uri."""
    marker = "PHANTOM_ID_TABLE_URI_BODY"

    def fake_resolve(
        slug_or_entity_id: str,
        *,
        include_non_active: bool = False,
        expected_digest: str | None = None,
        conn: object | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        del include_non_active, expected_digest, conn
        from implement_admission.skill_source_table import canonical_table_key

        key = canonical_table_key(slug_or_entity_id)
        if key == "ulg-architecture":
            return {
                "id": "agent_skill:ulg-architecture",
                "digest": "sha256:ulg-architecture",
                "body": marker,
            }, None
        if key == "cortex-orientation":
            return {
                "id": "rule:cortex-orientation",
                "digest": "sha256:cortex-orientation",
                "body": "orientation",
            }, None
        return None, "body_missing"

    monkeypatch.setattr(
        "implement_admission.skill_body_resolve.resolve_skill_body_from_table",
        fake_resolve,
    )
    resolution = resolve_injected_bodies(
        "claude-api",
        platform="api",
        inject_profile="dispatch",
        caller_skill_ids=("ulg-architecture",),
        budget_bytes=None,
    )
    assert marker in resolution.block_md
