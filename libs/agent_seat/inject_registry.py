"""Declarative scoped inject registry — single resolver for all server inject paths."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cortex_store.guidance_entity import entity_slug_from_id

from agent_seat.registry import is_lead_agent

from .body_injection import (
    INJECTED_BODY_BUDGET_BYTES,
    RequiredBodyUnresolved,
    _fetch_body_sync,
    _slug_from_entry,
)

# Migration: WEB_BOOT_INJECT_ENTITY_IDS, INVARIANT_PAIR_ENTITY_IDS, and
# CODING_SESSION_BUNDLE were folded into INJECT_REGISTRY (no parallel constants).

SENTINEL_DISPATCH_INJECT_ENTITY_ID = "agent_skill:sentinel-dispatch-inject-19887"

CODING_SESSION_ADVERTISE_SLUGS: tuple[str, ...] = (
    "implement-work-item",
    "git-posture",
    "service-lifecycle",
    "completion-provenance-discipline",
    "fs",
)

_DISPATCH_PACKET_PRIORITY_BASE = 60
_TIER_RANK = {"critical": 0, "must_inline": 1, "normal": 2}

_AGENT_SKILL_TOKEN_RE = re.compile(r"agent_skill:([a-z0-9][-a-z0-9_]*)", re.IGNORECASE)
_SKILL_PATH_RE = re.compile(
    r"(?:agent-skills|agent_skills)/([a-z0-9][-a-z0-9_]*)\.md",
    re.IGNORECASE,
)
_INVARIANTS_BLOCK_RE = re.compile(
    r"<invariants>(.*?)</invariants>",
    re.DOTALL | re.IGNORECASE,
)


class InjectScope(StrEnum):
    UNIVERSAL = "universal"
    LEAD = "lead"
    DISPATCH_PACKET = "dispatch_packet"
    CODING = "coding"
    LOADED_SET = "loaded_set"


class InlineTier(StrEnum):
    CRITICAL = "critical"
    MUST_INLINE = "must_inline"
    NORMAL = "normal"


@dataclass(frozen=True, slots=True)
class InjectEntry:
    entity_id: str
    scope: InjectScope
    platform_predicate: str
    profile_applicability: frozenset[str]
    priority: int
    lifecycle_required: bool = False
    inline_tier: InlineTier = InlineTier.NORMAL


INJECT_REGISTRY: tuple[InjectEntry, ...] = (
    InjectEntry(
        entity_id="rule:cortex-orientation",
        scope=InjectScope.UNIVERSAL,
        platform_predicate="*",
        profile_applicability=frozenset({"*"}),
        priority=10,
        inline_tier=InlineTier.CRITICAL,
    ),
    InjectEntry(
        entity_id="rule:cortex-provenance-discipline",
        scope=InjectScope.UNIVERSAL,
        platform_predicate="*",
        profile_applicability=frozenset({"*"}),
        priority=20,
        inline_tier=InlineTier.MUST_INLINE,
    ),
    InjectEntry(
        entity_id="rule:model-tier-awareness-web",
        scope=InjectScope.UNIVERSAL,
        platform_predicate="web",
        profile_applicability=frozenset({"*"}),
        priority=25,
        inline_tier=InlineTier.MUST_INLINE,
    ),
    InjectEntry(
        entity_id="rule:orchestrator-core",
        scope=InjectScope.LEAD,
        platform_predicate="*",
        profile_applicability=frozenset({"*"}),
        priority=30,
        lifecycle_required=True,
        inline_tier=InlineTier.MUST_INLINE,
    ),
    InjectEntry(
        entity_id="rule:orchestrator-workflow",
        scope=InjectScope.CODING,
        platform_predicate="*",
        profile_applicability=frozenset({"code_touching"}),
        priority=35,
        inline_tier=InlineTier.NORMAL,
    ),
    InjectEntry(
        entity_id="rule:architecture-invariants",
        scope=InjectScope.CODING,
        platform_predicate="*",
        profile_applicability=frozenset({"code_touching"}),
        priority=40,
        inline_tier=InlineTier.NORMAL,
    ),
    InjectEntry(
        entity_id="rule:ulg-architecture_ulg",
        scope=InjectScope.CODING,
        platform_predicate="*",
        profile_applicability=frozenset({"code_touching"}),
        priority=50,
        inline_tier=InlineTier.NORMAL,
    ),
)


def coding_scope_inject_entity_ids() -> tuple[str, ...]:
    return tuple(
        entry.entity_id
        for entry in INJECT_REGISTRY
        if entry.scope == InjectScope.CODING
    )


@dataclass
class InjectResolution:
    block_md: str
    injected: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)


def active_scopes(
    role: str | None,
    inject_profile: str | None,
    *,
    platform: str = "*",
) -> set[InjectScope]:
    """Return registry scopes eligible for server-side body inject.

    Web (claude.ai UI) and cursor (rules + stubs) no longer receive UNIVERSAL/LEAD
    static inject — operator attaches skills there. Dispatch packet invariants and
    coding-session bundles remain server-injected.
    """
    scopes: set[InjectScope] = set()
    if platform not in {"web", "cursor"}:
        scopes.add(InjectScope.UNIVERSAL)
        if role and is_lead_agent(role):
            scopes.add(InjectScope.LEAD)
    if inject_profile == "dispatch":
        scopes.add(InjectScope.DISPATCH_PACKET)
    return scopes


def _platform_match(entry: InjectEntry, platform: str) -> bool:
    pred = entry.platform_predicate
    if pred == "*":
        return True
    return pred == platform


def _applicability_ok(
    entry: InjectEntry,
    *,
    code_touching: bool,
    inject_profile: str | None,
) -> bool:
    if entry.scope == InjectScope.CODING:
        return code_touching
    apps = entry.profile_applicability
    if "*" in apps:
        return True
    if "code_touching" in apps:
        return code_touching
    if "dispatch" in apps:
        return inject_profile == "dispatch"
    return True


def parse_packet_invariant_skill_ids(packet_text: str) -> tuple[str, ...]:
    """Extract skill entity ids from a packet ``<invariants>`` block."""
    if not packet_text:
        return ()
    match = _INVARIANTS_BLOCK_RE.search(packet_text)
    block = match.group(1) if match else packet_text
    seen: set[str] = set()
    ordered: list[str] = []
    for pattern in (_AGENT_SKILL_TOKEN_RE, _SKILL_PATH_RE):
        for found in pattern.finditer(block):
            slug = found.group(1).lower()
            entity_id = f"agent_skill:{slug}"
            if entity_id not in seen:
                seen.add(entity_id)
                ordered.append(entity_id)
    return tuple(ordered)


def _tier_rank(tier: InlineTier) -> int:
    return _TIER_RANK[tier.value]


def _dedupe_entries(candidates: list[InjectEntry]) -> tuple[list[InjectEntry], int]:
    best: dict[str, InjectEntry] = {}
    collisions = 0
    for entry in candidates:
        existing = best.get(entry.entity_id)
        if existing is None:
            best[entry.entity_id] = entry
            continue
        collisions += 1
        if _tier_rank(entry.inline_tier) < _tier_rank(existing.inline_tier):
            best[entry.entity_id] = entry
        elif _tier_rank(entry.inline_tier) == _tier_rank(existing.inline_tier):
            if entry.priority < existing.priority:
                best[entry.entity_id] = entry
    return list(best.values()), collisions


def _fetch_registry_entry(
    entry: InjectEntry,
    *,
    metrics: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    include_non_active = not entry.lifecycle_required
    payload, reason = _fetch_body_sync(
        entry.entity_id,
        None,
        include_non_active=include_non_active,
    )
    if payload is None:
        if entry.lifecycle_required:
            return None, "inactive_lifecycle_withheld"
        return None, reason or "unreachable"
    digest = str(payload.get("digest") or "")
    body = str(payload.get("body") or "")
    if not digest or not body:
        if entry.lifecycle_required:
            return None, "inactive_lifecycle_withheld"
        return None, "body_missing"
    metrics["cold_fetches"] = int(metrics.get("cold_fetches", 0)) + 1
    slug = entity_slug_from_id(entry.entity_id)
    return {
        "id": entry.entity_id,
        "name": slug,
        "digest": digest,
        "body": body,
        "inline_tier": entry.inline_tier.value,
        "priority": entry.priority,
        "scope": entry.scope.value,
    }, None


def _index_entry(slug: str, entity_id: str) -> str:
    return (
        f"\n\n<!-- injected-index:{slug} entity_id={entity_id} -->"
        f"\n- `{slug}` — fs(cortex, md_read agent-skills/{slug}.md)"
    )


def _pack_tiered_bodies(
    ordered_rows: list[tuple[InjectEntry, dict[str, Any]]],
    *,
    budget_bytes: int | None,
    already_present: str,
    marker_prefix: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    blocks: list[str] = []
    injected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    overflow_ids: list[str] = []
    fail_closed_reason: str | None = None
    remaining = budget_bytes
    rendered_bytes = 0
    critical_bytes = 0

    for entry, row in ordered_rows:
        entity_id = str(row.get("id") or "")
        digest = str(row.get("digest") or "")
        body = row.get("body")
        if not entity_id or not digest or not isinstance(body, str):
            dropped.append({"id": entity_id or None, "reason": "body_missing"})
            continue
        slug = _slug_from_entry(row)
        marker = f"<!-- {marker_prefix}:{slug} digest:{digest} -->"
        if marker in already_present:
            continue
        block = f"\n\n{marker}\n```markdown\n{body}\n```"
        block_size = len(block.encode("utf-8"))
        tier = entry.inline_tier

        if budget_bytes is not None and block_size > (remaining or 0):
            if tier == InlineTier.CRITICAL:
                raise RequiredBodyUnresolved(
                    [{"id": entity_id, "reason": "budget", "tier": tier.value}]
                )
            if tier == InlineTier.MUST_INLINE:
                fail_closed_reason = fail_closed_reason or "must_inline_budget"
                blocks.append(
                    f"\n\n<!-- inject:FAIL_CLOSED entity_id={entity_id} "
                    f"reason=budget tier={tier.value} -->"
                )
                dropped.append({"id": entity_id, "reason": "budget_fail_closed"})
                continue
            overflow_ids.append(entity_id)
            blocks.append(_index_entry(slug, entity_id))
            dropped.append({"id": entity_id, "reason": "budget_index"})
            continue

        blocks.append(block)
        injected.append({"id": entity_id, "digest": digest, "bytes": block_size})
        rendered_bytes += block_size
        if tier in (InlineTier.CRITICAL, InlineTier.MUST_INLINE):
            critical_bytes += block_size
        if budget_bytes is not None:
            remaining = (remaining or 0) - block_size

    return (
        "".join(blocks),
        injected,
        dropped,
        {
            "rendered_bytes": rendered_bytes,
            "critical_bytes": critical_bytes,
            "overflow_ids": overflow_ids,
            "fail_closed_reason": fail_closed_reason,
        },
    )


def _candidate_entries(
    *,
    role: str | None,
    platform: str,
    inject_profile: str | None,
    code_touching: bool,
    packet_invariant_ids: tuple[str, ...],
    include_loaded_set: bool = False,
) -> list[InjectEntry]:
    scopes = active_scopes(role, inject_profile, platform=platform)
    if include_loaded_set:
        scopes = set(scopes) | {InjectScope.LOADED_SET}
    static = [
        entry
        for entry in INJECT_REGISTRY
        if (
            entry.scope in scopes
            or (entry.scope == InjectScope.CODING and code_touching)
        )
        and _platform_match(entry, platform)
        and _applicability_ok(
            entry, code_touching=code_touching, inject_profile=inject_profile
        )
    ]
    dynamic: list[InjectEntry] = []
    if InjectScope.DISPATCH_PACKET in scopes:
        for idx, entity_id in enumerate(packet_invariant_ids):
            dynamic.append(
                InjectEntry(
                    entity_id=entity_id,
                    scope=InjectScope.DISPATCH_PACKET,
                    platform_predicate="*",
                    profile_applicability=frozenset({"*"}),
                    priority=_DISPATCH_PACKET_PRIORITY_BASE + idx,
                    inline_tier=InlineTier.MUST_INLINE,
                )
            )
    deduped, _ = _dedupe_entries(static + dynamic)
    return deduped


def _sort_for_pack(
    entries: list[InjectEntry],
    packet_invariant_ids: tuple[str, ...],
) -> list[InjectEntry]:
    packet_order = {eid: i for i, eid in enumerate(packet_invariant_ids)}

    def sort_key(entry: InjectEntry) -> tuple[int, int, int, int, str]:
        tier = _tier_rank(entry.inline_tier)
        if (
            entry.scope == InjectScope.DISPATCH_PACKET
            and entry.entity_id in packet_order
        ):
            group = 1
            packet_idx = packet_order[entry.entity_id]
        elif tier <= _TIER_RANK["must_inline"]:
            group = 0
            packet_idx = 0
        else:
            group = 2
            packet_idx = 0
        return (group, tier, packet_idx, entry.priority, entry.entity_id)

    return sorted(entries, key=sort_key)


def resolve_injected_bodies(
    seat: str,
    *,
    role: str | None = None,
    platform: str = "*",
    inject_profile: str | None = None,
    code_touching: bool = False,
    packet_invariant_ids: tuple[str, ...] = (),
    budget_bytes: int | None = INJECTED_BODY_BUDGET_BYTES,
    already_present: str = "",
    marker_prefix: str = "invariant-skill",
) -> InjectResolution:
    """Single registry-driven resolver for all server inject paths."""
    del seat  # reserved for future seat-specific policy
    metrics: dict[str, Any] = {"cold_fetches": 0, "cache_hit": False}
    candidates = _candidate_entries(
        role=role,
        platform=platform,
        inject_profile=inject_profile,
        code_touching=code_touching,
        packet_invariant_ids=packet_invariant_ids,
    )
    deduped, dedupe_collisions = _dedupe_entries(candidates)
    scopes = active_scopes(role, inject_profile, platform=platform)
    ordered_entries = _sort_for_pack(deduped, packet_invariant_ids)

    resolved_rows: list[tuple[InjectEntry, dict[str, Any]]] = []
    dropped: list[dict[str, Any]] = []
    for entry in ordered_entries:
        row, reason = _fetch_registry_entry(entry, metrics=metrics)
        if row is None:
            dropped.append({"id": entry.entity_id, "reason": reason or "unreachable"})
            if entry.inline_tier == InlineTier.CRITICAL:
                raise RequiredBodyUnresolved(dropped)
            continue
        resolved_rows.append((entry, row))

    block_md, injected, pack_dropped, pack_meta = _pack_tiered_bodies(
        resolved_rows,
        budget_bytes=budget_bytes,
        already_present=already_present,
        marker_prefix=marker_prefix,
    )
    dropped.extend(pack_dropped)
    injected_ids = [str(i.get("id") or "") for i in injected if i.get("id")]
    telemetry = {
        **pack_meta,
        "injected_ids": injected_ids,
        "dedupe_collisions": dedupe_collisions,
        "scopes_active": sorted(s.value for s in scopes),
        "cold_fetches": metrics.get("cold_fetches", 0),
    }
    return InjectResolution(
        block_md=block_md,
        injected=injected,
        dropped=dropped,
        telemetry=telemetry,
    )


def injected_skill_slugs(
    *,
    role: str | None = None,
    platform: str = "*",
    inject_profile: str | None = None,
    packet_invariant_ids: tuple[str, ...] = (),
    code_touching: bool = False,
    include_loaded_set: bool = False,
) -> tuple[str, ...]:
    """Registry-derived slug set for skill_suggest loaded-set accounting."""
    candidates = _candidate_entries(
        role=role,
        platform=platform,
        inject_profile=inject_profile,
        code_touching=code_touching,
        packet_invariant_ids=packet_invariant_ids,
        include_loaded_set=include_loaded_set,
    )
    deduped, _ = _dedupe_entries(candidates)
    return tuple(sorted(entity_slug_from_id(entry.entity_id) for entry in deduped))
