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
    def fake_fetch(
        entity_id: str,
        _digest: str | None,
        *,
        include_non_active: bool = False,
        timeout_ms: int = 300,
    ) -> tuple[dict[str, Any] | None, str | None]:
        del include_non_active, timeout_ms
        if entity_id not in bodies:
            return None, "body_missing"
        digest = f"sha256:{entity_id.split(':')[-1]}"
        return {"digest": digest, "body": bodies[entity_id]}, None

    monkeypatch.setattr(
        "agent_seat.inject_registry._fetch_body_sync",
        fake_fetch,
    )


def test_parse_packet_invariant_skill_ids() -> None:
    packet = """
<invariants>
- agent_skill:sentinel-dispatch-inject-19887
- fs(cortex, md_read, agent-skills/architecture-invariants.md)
</invariants>
"""
    assert parse_packet_invariant_skill_ids(packet) == (
        "agent_skill:sentinel-dispatch-inject-19887",
        "agent_skill:architecture-invariants",
    )


def test_active_scopes_lead_dispatch_union() -> None:
    scopes = active_scopes("claude-web", "dispatch")
    assert scopes == {
        InjectScope.UNIVERSAL,
        InjectScope.LEAD,
        InjectScope.DISPATCH_PACKET,
    }


def test_web_boot_injects_model_tier_awareness_web(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tier_marker = "MODEL_TIER_AWARENESS_WEB_MUST_INLINE_MARKER"
    bodies = {
        "agent_skill:cortex-orientation": "orientation",
        "agent_skill:cortex-provenance-discipline": "provenance",
        "agent_skill:model-tier-awareness-web": tier_marker,
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-web",
        platform="web",
        budget_bytes=None,
    )
    injected_ids = [item["id"] for item in resolution.injected]
    assert "agent_skill:model-tier-awareness-web" in injected_ids
    assert tier_marker in resolution.block_md


def test_union_matrix_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "orientation",
        "agent_skill:cortex-provenance-discipline": "provenance",
        "agent_skill:model-tier-awareness-web": "tier-web",
        "agent_skill:orchestrator-workflow": "orchestrator",
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
    assert set(injected_ids) == set(bodies)


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
            "claude-web",
            platform="web",
            budget_bytes=1000,
        )


def test_must_inline_emits_fail_closed_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = {
        "agent_skill:cortex-orientation": "critical-small",
        "agent_skill:cortex-provenance-discipline": "y" * 20_000,
    }
    _body_map(monkeypatch, bodies)
    resolution = resolve_injected_bodies(
        "claude-web",
        platform="web",
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
    def fake_fetch(
        entity_id: str,
        _digest: str | None,
        *,
        include_non_active: bool = False,
        timeout_ms: int = 300,
    ) -> tuple[dict[str, Any] | None, str | None]:
        del timeout_ms
        if entity_id == "agent_skill:orchestrator-workflow" and not include_non_active:
            return None, "body_missing"
        if entity_id == "agent_skill:cortex-orientation":
            return {"digest": "sha256:o", "body": "orientation"}, None
        if entity_id == "agent_skill:cortex-provenance-discipline":
            return {"digest": "sha256:p", "body": "provenance"}, None
        return None, "body_missing"

    monkeypatch.setattr("agent_seat.inject_registry._fetch_body_sync", fake_fetch)
    resolution = resolve_injected_bodies(
        "claude-web",
        role="claude-web",
        platform="web",
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
    boot_rendered = boot_result.get("auto_inject_skills_md") or ""
    assert sentinel_body in boot_rendered, (
        "sentinel missing from run_cortex_boot(profile=dispatch) rendered inject block"
    )

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
async def test_lead_web_boot_injects_orchestrator_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """role=lead web boot must inject the LEAD-scope orchestrator-workflow body.

    Regression (step 4): the boot forwarded the functional role label "lead" into
    the resolver, but LEAD-scope activation is ``is_lead_agent(seat)`` — seat
    membership (agents.yaml lead_seats), not the label — so LEAD-scope skills were
    silently skipped. With the wiring fixed, the seat slug drives lead-determination.
    """
    orch_marker = "ORCHESTRATOR_WORKFLOW_LEAD_INJECT_MARKER"
    tier_marker = "MODEL_TIER_AWARENESS_WEB_LEAD_BOOT_MARKER"
    bodies = {
        "agent_skill:cortex-orientation": "orientation",
        "agent_skill:cortex-provenance-discipline": "provenance",
        "agent_skill:model-tier-awareness-web": tier_marker,
        "agent_skill:orchestrator-workflow": orch_marker,
    }
    _body_map(monkeypatch, bodies)
    # Lead-ness is seat membership; pin claude-web as a lead seat deterministically
    # (independent of the agents.yaml in the test environment).
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
    rendered = boot_result.get("auto_inject_skills_md") or ""
    assert orch_marker in rendered, (
        "orchestrator-workflow (LEAD scope) missing from role=lead web boot "
        "auto_inject block — lead-scope activation regressed"
    )
    assert tier_marker in rendered, (
        "model-tier-awareness-web missing from role=lead web boot auto_inject block"
    )
