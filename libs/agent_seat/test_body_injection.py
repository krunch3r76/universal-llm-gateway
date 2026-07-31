"""G3 inline-only body injection tests (T1–T17)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from cortex_store.routes._skill_suggest import (
    norm_loaded,
    run_stage_a,
)
from gen_rules.agent_guides import AGENT_GUIDES_RULE_SLUGS
from claude_bundles.catalog import get_skill_catalog
from implement_admission.skill_catalog_resolver import (
    canonical_agent_skill_id,
    canonical_catalog_slug,
)

from agent_seat.body_injection import (
    INJECTED_BODY_BUDGET_BYTES,
    RequiredBodyUnresolved,
    SkillDeliveryChannel,
    _fetch_body_sync,
    _fetch_invariant_entries_for,
    append_invariant_pair_bodies,
    build_dispatch_skill_context,
    build_injected_bodies_md,
    clear_payload_cache_for_tests,
    emit_layer_a_fs_line,
    fetch_invariant_pair_entries,
    filter_double_load_excluded,
    resolve_inline_only_bodies,
    select_skill_delivery_channel,
    web_auto_inject_skill_slugs,
)
from agent_seat.inject_registry import coding_scope_inject_entity_ids
from agent_seat.prompts import assemble_system_prompt

_REPO = Path(__file__).resolve().parents[2]


def _entry(
    entity_id: str,
    *,
    name: str | None = None,
    digest: str = "sha256:abc",
    body: str = "body",
    delivery_priority: int = 100,
    delivery_criticality: str | None = None,
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "name": name or entity_id.split(":", 1)[-1],
        "digest": digest,
        "body": body,
        "delivery_priority": delivery_priority,
        "delivery_criticality": delivery_criticality,
    }


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_payload_cache_for_tests()


def test_assemble_injects_bodies_part() -> None:
    injected = "<!-- injected-body:rule:foo digest:sha256:abc -->"
    system = assemble_system_prompt(
        "gatherer",
        briefing_card_md="# Briefing",
        continuation_md="# Continuation",
        injected_bodies_md=injected,
    )
    assert (
        system.index("Briefing") < system.index(injected) < system.index("Continuation")
    )


def test_assemble_is_io_free(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(
        "agent_seat.prompts.make_sync_client",
        lambda *a, **k: client,
        raising=False,
    )
    assemble_system_prompt("gatherer", injected_bodies_md="X")
    client.get.assert_not_called()


def test_resolve_covers_rules_and_invariants(monkeypatch: pytest.MonkeyPatch) -> None:
    index = [
        {
            "id": "agent_skill:architecture-invariants",
            "name": "architecture-invariants",
            "digest": "sha256:inv",
            "delivery_priority": 0,
        },
        {
            "id": "rule:system-conduct",
            "name": "system-conduct",
            "digest": "sha256:rule",
            "delivery_priority": 100,
        },
    ]
    bodies = {
        ("agent_skill:architecture-invariants", "sha256:inv"): "invariant body",
        ("rule:system-conduct", "sha256:rule"): "rule body",
    }

    def fake_index(seat: str, layer: str = "all", **_: Any) -> list[dict[str, Any]]:
        assert layer == "all"
        return index

    def fake_body(
        entity_id: str, expected_digest: str | None, **_: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        body = bodies.get((entity_id, expected_digest or ""))
        if body is None:
            return None, "body_missing"
        return {"body": body, "digest": expected_digest}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    block, injected, _, _ = resolve_inline_only_bodies("grok-api-multi")
    assert "invariant body" in block
    assert "rule body" in block
    assert len(injected) == 2


def test_priority_by_delivery_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    index = [
        {
            "id": "rule:zzz-late",
            "name": "aaa-name",
            "digest": "sha256:z",
            "delivery_priority": 50,
        },
        {
            "id": "rule:aaa-early",
            "name": "zzz-name",
            "digest": "sha256:a",
            "delivery_priority": 200,
        },
        {
            "id": "agent_skill:architecture-invariants",
            "name": "architecture-invariants",
            "digest": "sha256:i0",
            "delivery_priority": 0,
        },
        {
            "id": "agent_skill:ulg-architecture",
            "name": "ulg-architecture",
            "digest": "sha256:i1",
            "delivery_priority": 1,
        },
    ]
    order: list[str] = []

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return index

    def fake_body(entity_id: str, digest: str | None, **_: Any) -> tuple[Any, Any]:
        order.append(entity_id)
        return {"body": f"body-{entity_id}", "digest": digest}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    resolve_inline_only_bodies("grok-api-multi", budget_bytes=1_000_000)
    assert order[0] == "agent_skill:architecture-invariants"
    assert order[1] == "agent_skill:ulg-architecture"
    assert order[2] == "rule:zzz-late"
    assert order[3] == "rule:aaa-early"


def test_continue_after_drop() -> None:
    huge = "x" * 20_000
    small = "y" * 100
    entries = [
        _entry("rule:heavy", body=huge, delivery_priority=200, digest="sha256:h"),
        _entry("rule:light", body=small, delivery_priority=0, digest="sha256:l"),
    ]
    block, injected, dropped = build_injected_bodies_md(
        "grok-api-multi",
        entries,
        budget_bytes=5000,
    )
    assert "y" * 100 in block
    assert "x" * 20_000 not in block
    assert any(d["id"] == "rule:heavy" and d["reason"] == "budget" for d in dropped)
    assert any(i["id"] == "rule:light" for i in injected)


def test_body_fetch_passes_expected_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "rule:drift",
                "name": "drift",
                "digest": "sha256:expected",
                "delivery_priority": 0,
            }
        ]

    def fake_body(
        entity_id: str, expected_digest: str | None, **_: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        seen.append(expected_digest)
        return None, "digest_mismatch"

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    _, _, dropped, _ = resolve_inline_only_bodies("grok-api-multi")
    assert seen == ["sha256:expected"]
    assert dropped == [{"id": "rule:drift", "reason": "digest_mismatch"}]


def test_fail_open_on_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_seat.body_injection._fetch_skill_index_sync",
        lambda *a, **k: [],
    )
    block, injected, dropped, metrics = resolve_inline_only_bodies("grok-api-multi")
    assert block == ""
    assert injected == []
    assert dropped[0]["reason"] == "unreachable"
    assert "elapsed_ms" in metrics


def test_required_criticality_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_seat.body_injection._fetch_skill_index_sync",
        lambda *a, **k: [
            {
                "id": "rule:critical",
                "name": "critical",
                "digest": "sha256:c",
                "delivery_priority": 0,
                "delivery_criticality": "required",
            }
        ],
    )
    monkeypatch.setattr(
        "agent_seat.body_injection._fetch_body_sync",
        lambda *a, **k: (None, "body_missing"),
    )
    with pytest.raises(RequiredBodyUnresolved):
        resolve_inline_only_bodies("grok-api-multi")


def test_payload_cache_keyed_by_id_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    index = [
        {
            "id": "rule:cached",
            "name": "cached",
            "digest": "sha256:v1",
            "delivery_priority": 0,
        }
    ]

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return index

    def fake_body(entity_id: str, digest: str | None, **_: Any) -> tuple[Any, Any]:
        calls.append(f"{entity_id}:{digest}")
        return {"body": "payload", "digest": digest}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    resolve_inline_only_bodies("grok-api-multi")
    assert calls == ["rule:cached:sha256:v1"]
    resolve_inline_only_bodies("grok-api-multi")
    assert calls == ["rule:cached:sha256:v1"]

    index[0]["digest"] = "sha256:v2"
    resolve_inline_only_bodies("grok-api-multi")
    assert calls == [
        "rule:cached:sha256:v1",
        "rule:cached:sha256:v2",
    ]


def test_dedup_matches_marker_not_raw_digest() -> None:
    digest = "sha256:deadbeef"
    entries = [_entry("rule:foo", digest=digest, body="inject me")]
    raw_present = f"index shows digest {digest} without marker"
    block_raw, injected_raw, _ = build_injected_bodies_md(
        "seat", entries, already_present=raw_present
    )
    assert injected_raw
    assert digest in block_raw

    marker_present = f"<!-- injected-body:foo digest:{digest} -->"
    block_marker, injected_marker, _ = build_injected_bodies_md(
        "seat", entries, already_present=marker_present
    )
    assert injected_marker == []
    assert block_marker == ""


def test_cache_output_differs_when_already_present_differs() -> None:
    entries = [_entry("rule:foo", digest="sha256:same", body="same body")]
    block_a, _, _ = build_injected_bodies_md("seat", entries, already_present="")
    block_b, _, _ = build_injected_bodies_md(
        "seat",
        entries,
        already_present="<!-- injected-body:foo digest:sha256:same -->",
    )
    assert block_a != block_b
    assert block_b == ""


def test_total_deadline_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    index = [
        {
            "id": f"rule:{i}",
            "name": f"r{i}",
            "digest": f"sha256:{i}",
            "delivery_priority": i,
        }
        for i in range(5)
    ]

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return index

    def slow_body(*_: Any, **__: Any) -> tuple[Any, Any]:
        time.sleep(0.05)
        return {"body": "b", "digest": "sha256:0"}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", slow_body)

    _, _, dropped, metrics = resolve_inline_only_bodies(
        "grok-api-multi",
        total_deadline_ms=60,
    )
    assert metrics["deadline_hit"] is True
    assert any(d["reason"] == "timeout" for d in dropped)


def test_launch_corpus_survivor_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """T15 — real 11-rule + 2-skill corpus exceeds default budget."""
    index: list[dict[str, Any]] = [
        {
            "id": "agent_skill:architecture-invariants",
            "name": "architecture-invariants",
            "digest": "sha256:inv0",
            "delivery_priority": 0,
        },
        {
            "id": "agent_skill:ulg-architecture",
            "name": "ulg-architecture",
            "digest": "sha256:inv1",
            "delivery_priority": 1,
        },
    ]
    bodies: dict[str, str] = {}
    for slug in sorted(AGENT_GUIDES_RULE_SLUGS):
        path = _REPO / "docs" / "agent-guides" / "rules" / f"{slug}.md"
        text = path.read_text(encoding="utf-8") if path.is_file() else f"# {slug}\n"
        bodies[f"rule:{slug}"] = text
        index.append(
            {
                "id": f"rule:{slug}",
                "name": slug,
                "digest": f"sha256:{slug}",
                "delivery_priority": 100,
            }
        )
    for inv in ("architecture-invariants", "ulg-architecture"):
        path = _REPO / "docs" / "agent-guides" / "skills" / f"{inv}.md"
        bodies[f"agent_skill:{inv}"] = (
            path.read_text(encoding="utf-8") if path.is_file() else f"# {inv}\n"
        )

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return index

    def fake_body(entity_id: str, digest: str | None, **_: Any) -> tuple[Any, Any]:
        return {"body": bodies[entity_id], "digest": digest}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    _, injected, dropped, _ = resolve_inline_only_bodies(
        "grok-api-multi",
        budget_bytes=INJECTED_BODY_BUDGET_BYTES,
    )
    injected_ids = [i["id"] for i in injected]
    assert injected_ids[0] == "agent_skill:architecture-invariants"
    assert injected_ids[1] == "agent_skill:ulg-architecture"
    running = 0
    expected: list[str] = []
    sorted_rows = sorted(
        index,
        key=lambda r: (
            r.get("delivery_priority")
            if r.get("delivery_priority") is not None
            else 100,
            str(r.get("name")),
        ),
    )
    for row in sorted_rows:
        eid = row["id"]
        slug = row["name"]
        digest = row["digest"]
        body = bodies[eid]
        size = len(
            f"\n\n<!-- injected-body:{slug} digest:{digest} -->\n```markdown\n{body}\n```"
        )
        if running + size <= INJECTED_BODY_BUDGET_BYTES:
            running += size
            expected.append(eid)
        else:
            # continue-after-drop: oversized row is skipped; smaller rows may still fit
            continue
    assert injected_ids == expected
    assert any(d.get("reason") == "budget" for d in dropped)


def test_web_auto_inject_skill_slugs_empty() -> None:
    assert web_auto_inject_skill_slugs() == ()


@pytest.mark.offline
def test_web_skill_suggest_preload_set_is_orientation_channels_only() -> None:
    assert web_auto_inject_skill_slugs() == ()
    from agent_seat.inject_channels import web_seat_injected_skill_slugs

    preloaded = frozenset(norm_loaded(s) for s in web_seat_injected_skill_slugs("claude-web"))
    assert "cortex-orientation" not in preloaded
    assert "orchestrator-core" not in preloaded
    assert "operator-posture" not in preloaded
    no_context = run_stage_a(
        agent="claude-web",
        loaded=[],
        conversation_context="",
        limit=8,
    )
    assert "cortex-orientation" not in no_context["seat_preloaded"]
    assert "orchestrator-core" not in no_context["seat_preloaded"]


def test_append_invariant_pair_bodies_adds_sentinel_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_seat.inject_registry import InjectResolution

    def fake_resolve(_seat: str, **_: object) -> InjectResolution:
        block = (
            "\n\n<!-- invariant-skill:architecture-invariants digest:sha256:inv0 -->"
            "\n```markdown\narch body\n```"
            "\n\n<!-- invariant-skill:ulg-architecture digest:sha256:inv1 -->"
            "\n```markdown\nulg body\n```"
        )
        return InjectResolution(
            block_md=block,
            injected=[
                {"id": "agent_skill:architecture-invariants", "digest": "sha256:inv0"},
                {"id": "agent_skill:ulg-architecture", "digest": "sha256:inv1"},
            ],
            dropped=[],
            telemetry={},
        )

    monkeypatch.setattr(
        "agent_seat.inject_registry.resolve_injected_bodies",
        fake_resolve,
    )
    updated, meta = append_invariant_pair_bodies("")
    assert updated.count("cortex:invariant-skills-autoappend") == 1
    assert "arch body" in updated
    assert "ulg body" in updated
    assert len(meta["injected"]) == 2


def test_append_invariant_pair_bodies_dedups_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_seat.inject_registry import InjectResolution

    digest = "sha256:inv0"
    present = f"<!-- invariant-skill:architecture-invariants digest:{digest} -->"

    def fake_resolve(_seat: str, *_: object, already_present: str = "", **__: object) -> InjectResolution:
        if "architecture-invariants" in already_present:
            return InjectResolution(block_md="", injected=[], dropped=[], telemetry={})
        return InjectResolution(
            block_md="\n```markdown\narch body\n```",
            injected=[{"id": "agent_skill:architecture-invariants", "digest": digest}],
            dropped=[],
            telemetry={},
        )

    monkeypatch.setattr(
        "agent_seat.inject_registry.resolve_injected_bodies",
        fake_resolve,
    )
    updated, meta = append_invariant_pair_bodies("", already_present=present)
    assert updated == ""
    assert meta["injected"] == []


def test_fetch_invariant_pair_entries_only_arch_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_body(entity_id: str, expected_digest: str | None, **_: Any) -> tuple[Any, Any]:
        seen.append(entity_id)
        return {"body": f"body-{entity_id}", "digest": "sha256:x"}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)
    fetch_invariant_pair_entries()
    assert seen == [
        "rule:orchestrator-workflow",
        "rule:architecture-invariants",
        "rule:ulg-architecture",
        "agent_skill:docstring-quality",
    ]


def test_fetch_body_sync_omits_include_non_active_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_flags: list[bool] = []

    def fake_resolve(
        _entity_id: str,
        *,
        include_non_active: bool = False,
        expected_digest: str | None = None,
        conn: object | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        del expected_digest, conn
        seen_flags.append(include_non_active)
        return {"body": "active body", "digest": "sha256:active"}, None

    monkeypatch.setattr(
        "implement_admission.skill_body_resolve.resolve_skill_body_from_catalog",
        fake_resolve,
    )

    _fetch_body_sync("agent_skill:architecture-invariants", None)
    assert seen_flags == [False]


def test_fetch_body_sync_passes_include_non_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_flags: list[bool] = []

    def fake_resolve(
        _entity_id: str,
        *,
        include_non_active: bool = False,
        expected_digest: str | None = None,
        conn: object | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        del expected_digest, conn
        seen_flags.append(include_non_active)
        return {"body": "draft body", "digest": "sha256:draft"}, None

    monkeypatch.setattr(
        "implement_admission.skill_body_resolve.resolve_skill_body_from_catalog",
        fake_resolve,
    )

    _fetch_body_sync(
        "agent_skill:architecture-invariants",
        None,
        include_non_active=True,
    )
    assert seen_flags == [True]


def test_fetch_invariant_entries_requests_non_active_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flags: list[bool] = []

    def fake_body(
        entity_id: str,
        expected_digest: str | None,
        *,
        include_non_active: bool = False,
        **_: Any,
    ) -> tuple[dict[str, Any] | None, str | None]:
        flags.append(include_non_active)
        if not include_non_active:
            return {"body": "", "digest": ""}, None
        return {
            "body": f"body-{entity_id}",
            "digest": "sha256:inactive",
        }, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    entries = _fetch_invariant_entries_for(
        ("agent_skill:architecture-invariants", "agent_skill:ulg-architecture")
    )
    assert flags == [True, True]
    assert len(entries) == 2
    assert entries[0]["body"] == "body-agent_skill:architecture-invariants"
    assert entries[1]["body"] == "body-agent_skill:ulg-architecture"


@pytest.mark.offline
def test_select_channel_layer_c_for_no_fs_generate() -> None:
    ctx = build_dispatch_skill_context(
        model="openai/gpt-5.5",
        mcp_enabled=False,
        inject_profile="dispatch",
        code_touching=True,
    )
    assert (
        select_skill_delivery_channel("architecture-invariants", ctx)
        == SkillDeliveryChannel.LAYER_C_BODY
    )


@pytest.mark.offline
def test_select_channel_layer_a_for_cursor_sdk() -> None:
    ctx = build_dispatch_skill_context(
        model="anthropic/claude-sonnet-4-6",
        mcp_enabled=True,
        role="cursor-sdk",
        platform="cursor",
        inject_profile="dispatch",
        code_touching=True,
    )
    assert (
        select_skill_delivery_channel("architecture-invariants", ctx)
        == SkillDeliveryChannel.LAYER_A_FS
    )
    line = emit_layer_a_fs_line("rule:architecture-invariants")
    assert "Use the `architecture-invariants` skill" in line
    assert "fs(" not in line
    assert "agent-skills/" not in line


@pytest.mark.offline
def test_layer_b_suppresses_layer_c_when_mount_verified() -> None:
    ctx = build_dispatch_skill_context(
        model="openai/gpt-5.5",
        mcp_enabled=False,
        provider_mount_slugs=frozenset({"architecture-invariants"}),
    )
    assert (
        select_skill_delivery_channel("architecture-invariants", ctx)
        == SkillDeliveryChannel.LAYER_B_PROVIDER
    )


@pytest.mark.offline
def test_double_load_exclusion_rule_alias() -> None:
    key = canonical_agent_skill_id("rule:architecture-invariants")
    kept = filter_double_load_excluded(
        ("architecture-invariants",),
        already_delivered=frozenset({key}),
    )
    assert kept == ()


@pytest.mark.offline
def test_gpt55_generate_mandatory_coding_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "rule:cortex-orientation": "orientation body",
        "rule:cortex-provenance-discipline": "provenance body",
        "rule:orchestrator-workflow": "orch workflow body",
        "rule:architecture-invariants": "arch invariants body",
        "rule:ulg-architecture": "ulg body",
    }

    def fake_resolve(
        slug_or_entity_id: str,
        *,
        include_non_active: bool = False,
        expected_digest: str | None = None,
        conn: object | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        del include_non_active, expected_digest, conn
        lookup = slug_or_entity_id
        if lookup not in bodies and ":" in lookup:
            lookup = f"rule:{lookup.split(':', 1)[1]}"
        if lookup not in bodies:
            lookup = f"rule:{slug_or_entity_id.split(':', 1)[-1]}"
        body = bodies.get(lookup)
        if body is None:
            return None, "body_missing"
        slug = lookup.split(":", 1)[-1]
        return {"digest": f"sha256:{slug}", "body": body, "id": lookup}, None

    monkeypatch.setattr(
        "implement_admission.skill_body_resolve.resolve_skill_body_from_catalog",
        fake_resolve,
    )
    from agent_seat.inject_registry import resolve_injected_bodies

    resolution = resolve_injected_bodies(
        "gatherer",
        role="gatherer",
        platform="api",
        inject_profile="dispatch",
        code_touching=True,
        budget_bytes=None,
        inline_only_dispatch=True,
    )
    block = resolution.block_md
    assert "arch invariants body" in block
    assert "orch workflow body" in block
    assert "ulg body" in block


_ALIAS_PARAMETRIZE = tuple(get_skill_catalog().alias_to_canonical.items()) + (
    ("synthetic-boot-alias", "orchestrator-workflow"),
)


@pytest.mark.parametrize("alias,canonical", _ALIAS_PARAMETRIZE)
@pytest.mark.offline
def test_t1_alias_routes_layer_b_when_canonical_mounted(
    alias: str,
    canonical: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if alias == "synthetic-boot-alias":
        monkeypatch.setitem(get_skill_catalog().alias_to_canonical, alias, canonical)
    ctx = build_dispatch_skill_context(
        model="openai/gpt-5.5",
        mcp_enabled=False,
        provider_mount_slugs=frozenset({canonical}),
    )
    for form in (alias, f"agent_skill:{alias}"):
        assert (
            select_skill_delivery_channel(form, ctx)
            == SkillDeliveryChannel.LAYER_B_PROVIDER
        )


@pytest.mark.parametrize("alias,canonical", _ALIAS_PARAMETRIZE)
@pytest.mark.offline
def test_t2_emit_layer_a_fs_line_alias_equals_canonical(
    alias: str,
    canonical: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if alias == "synthetic-boot-alias":
        monkeypatch.setitem(get_skill_catalog().alias_to_canonical, alias, canonical)
    assert emit_layer_a_fs_line(alias) == emit_layer_a_fs_line(canonical)
    assert emit_layer_a_fs_line(f"agent_skill:{alias}") == emit_layer_a_fs_line(
        f"agent_skill:{canonical}"
    )


@pytest.mark.parametrize("alias,canonical", _ALIAS_PARAMETRIZE)
@pytest.mark.offline
def test_t3_filter_double_load_excludes_alias_when_canonical_delivered(
    alias: str,
    canonical: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if alias == "synthetic-boot-alias":
        monkeypatch.setitem(get_skill_catalog().alias_to_canonical, alias, canonical)
    key = canonical_agent_skill_id(canonical)
    for form in (alias, f"agent_skill:{alias}"):
        kept = filter_double_load_excluded(
            (form,),
            already_delivered=frozenset({key}),
        )
        assert kept == ()


@pytest.mark.offline
def test_t4_context_build_normalizes_provider_mount_slugs() -> None:
    ctx = build_dispatch_skill_context(
        model="openai/gpt-5.5",
        mcp_enabled=False,
        provider_mount_slugs=frozenset(
            {"ulg-architecture", "session-close-kernel", "rule:ulg-architecture"}
        ),
    )
    assert ctx.provider_mount_slugs == frozenset(
        {"ulg-architecture", "session-close-kernel"}
    )


@pytest.mark.parametrize("alias,canonical", _ALIAS_PARAMETRIZE)
@pytest.mark.offline
def test_t5_registry_uniformity_over_coding_scope(
    alias: str,
    canonical: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if alias == "synthetic-boot-alias":
        monkeypatch.setitem(get_skill_catalog().alias_to_canonical, alias, canonical)
    registry_ids = [
        eid
        for eid in coding_scope_inject_entity_ids()
        if canonical_catalog_slug(eid) == canonical
    ]
    if not registry_ids:
        pytest.skip(f"no coding-scope registry entry for canonical {canonical!r}")
    ctx = build_dispatch_skill_context(
        model="openai/gpt-5.5",
        mcp_enabled=False,
        provider_mount_slugs=frozenset({canonical}),
    )
    for eid in registry_ids:
        for form in (alias, f"agent_skill:{alias}", eid):
            assert select_skill_delivery_channel(form, ctx) == (
                select_skill_delivery_channel(eid, ctx)
            )
            assert emit_layer_a_fs_line(form) == emit_layer_a_fs_line(eid)


@pytest.mark.offline
def test_partition_skill_channels_via_skills_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from implement_admission.skill_catalog_resolver import canonical_agent_skill_id

    from agent_seat.skills_merge import EffectiveSkill, partition_skill_channels

    monkeypatch.setattr(
        "agent_seat.skills_merge._read_mount_backend",
        lambda _model: "none",
    )
    effective = (
        EffectiveSkill(
            requested_id="architecture-invariants",
            canonical_id=canonical_agent_skill_id("architecture-invariants"),
            origin="caller",
        ),
    )
    partition = partition_skill_channels(
        effective,
        model="anthropic/claude-opus-4-8",
        mcp_enabled=True,
        role="reviewer",
        platform="api",
        inject_profile="dispatch",
        code_touching=True,
    )
    channel = select_skill_delivery_channel(
        "architecture-invariants",
        build_dispatch_skill_context(
            model="anthropic/claude-opus-4-8",
            mcp_enabled=True,
            role="reviewer",
            platform="api",
            inject_profile="dispatch",
            code_touching=True,
        ),
    )
    assert partition.rows[0].channel == channel.value
