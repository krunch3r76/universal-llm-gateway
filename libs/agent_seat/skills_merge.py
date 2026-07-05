"""Unified skills= merge and capability-selected channel partition (S1.c+S1.e)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from implement_admission.skill_source_table import canonical_agent_skill_id

from agent_seat.body_injection import (
    SkillDeliveryChannel,
    build_dispatch_skill_context,
    select_skill_delivery_channel,
)
from agent_seat.guidance_entity import entity_slug_from_id

Origin = Literal["caller", "scope_default"]
Channel = Literal["layer_a", "layer_b", "layer_c", "none"]
Disposition = Literal["delivered", "dropped"]

_VALID_MOUNT_BACKENDS = frozenset({"openai_container", "none"})


class SkillsMountBackendInvalidError(ValueError):
    """Raised when ``skills_mount_backend`` carries an out-of-enum value."""

    def __init__(self, model: str, value: str) -> None:
        self.model = model
        self.value = value
        super().__init__(
            f"capability_card_value_invalid: model={model!r} "
            f"capability_field='skills_mount_backend' value={value!r}"
        )


class McpPredicatedSkillsRejectedError(ValueError):
    """Caller-supplied MCP-predicated skills on a non-MCP dispatch."""

    def __init__(self, skills: tuple[str, ...]) -> None:
        self.skills = skills
        super().__init__(
            f"MCP-predicated caller skills rejected on non-MCP dispatch: {list(skills)}"
        )


@dataclass(frozen=True, slots=True)
class EffectiveSkill:
    requested_id: str
    canonical_id: str
    origin: Origin


@dataclass(frozen=True, slots=True)
class SkillChannelRow:
    requested_id: str
    canonical_id: str
    origin: Origin
    channel: Channel
    disposition: Disposition
    drop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SkillPartition:
    layer_b: tuple[str, ...]
    layer_a: tuple[str, ...]
    layer_c: tuple[str, ...]
    provider_mount_slugs: frozenset[str]
    rows: tuple[SkillChannelRow, ...]


def normalize_caller_skill_ids(caller_skills: list[str] | None) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in (caller_skills or []) if str(item).strip())


def resolve_effective_skills(
    caller_skills: list[str] | None,
    *,
    role: str | None,
    platform: str,
    inject_profile: str | None,
    code_touching: bool,
    packet_invariant_ids: tuple[str, ...],
) -> tuple[EffectiveSkill, ...]:
    """Merge caller ``skills=`` with scope defaults; dedupe by canonical agent_skill id."""
    from agent_seat.inject_registry import scope_default_skill_ids

    caller_ids = normalize_caller_skill_ids(caller_skills)
    default_entity_ids = scope_default_skill_ids(
        role,
        platform,
        inject_profile,
        code_touching,
        packet_invariant_ids,
    )

    seen: dict[str, EffectiveSkill] = {}
    order: list[str] = []

    for requested in caller_ids:
        canonical = canonical_agent_skill_id(requested)
        if canonical in seen:
            continue
        seen[canonical] = EffectiveSkill(
            requested_id=requested,
            canonical_id=canonical,
            origin="caller",
        )
        order.append(canonical)

    for entity_id in default_entity_ids:
        canonical = canonical_agent_skill_id(entity_id)
        if canonical in seen:
            continue
        slug = entity_slug_from_id(entity_id)
        seen[canonical] = EffectiveSkill(
            requested_id=slug,
            canonical_id=canonical,
            origin="scope_default",
        )
        order.append(canonical)

    return tuple(seen[key] for key in order)


def enforce_mcp_predicated_skills(
    effective: tuple[EffectiveSkill, ...],
    *,
    mcp_enabled: bool,
) -> tuple[tuple[EffectiveSkill, ...], tuple[SkillChannelRow, ...]]:
    """Reject caller-origin predicated skills; skip scope-default predicated ones."""
    if mcp_enabled:
        return effective, ()

    from implement_admission.skill_mcp_classification import skill_mcp_predicated

    caller_offenders: list[str] = []
    filtered: list[EffectiveSkill] = []
    skip_rows: list[SkillChannelRow] = []

    for skill in effective:
        if skill_mcp_predicated(skill.canonical_id):
            if skill.origin == "caller":
                caller_offenders.append(skill.requested_id)
            else:
                skip_rows.append(
                    SkillChannelRow(
                        requested_id=skill.requested_id,
                        canonical_id=skill.canonical_id,
                        origin=skill.origin,
                        channel="none",
                        disposition="dropped",
                        drop_reason="mcp_predicated_skip",
                    )
                )
        else:
            filtered.append(skill)

    if caller_offenders:
        raise McpPredicatedSkillsRejectedError(tuple(caller_offenders))

    return tuple(filtered), tuple(skip_rows)


def _read_mount_backend(model: str) -> str:
    from model_capabilities import skills_mount_backend

    backend = skills_mount_backend(model)
    if backend not in _VALID_MOUNT_BACKENDS:
        raise SkillsMountBackendInvalidError(model, backend)
    return backend


def partition_skill_channels(
    effective: tuple[EffectiveSkill, ...],
    *,
    model: str,
    mcp_enabled: bool,
    role: str | None,
    platform: str,
    inject_profile: str | None,
    code_touching: bool,
) -> SkillPartition:
    """Route each effective skill via ``select_skill_delivery_channel`` B>C>A."""
    backend = _read_mount_backend(model)
    if backend == "openai_container":
        mount_slugs = frozenset(skill.requested_id for skill in effective)
    else:
        mount_slugs = frozenset()

    ctx = build_dispatch_skill_context(
        model=model,
        mcp_enabled=mcp_enabled,
        role=role,
        platform=platform,
        inject_profile=inject_profile,
        code_touching=code_touching,
        provider_mount_slugs=mount_slugs,
    )

    layer_b: list[str] = []
    layer_a: list[str] = []
    layer_c: list[str] = []
    rows: list[SkillChannelRow] = []

    for skill in effective:
        channel = select_skill_delivery_channel(skill.requested_id, ctx)
        if channel == SkillDeliveryChannel.LAYER_B_PROVIDER:
            layer_b.append(skill.requested_id)
            wire_channel: Channel = "layer_b"
        elif channel == SkillDeliveryChannel.LAYER_C_BODY:
            layer_c.append(skill.requested_id)
            wire_channel = "layer_c"
        else:
            layer_a.append(skill.requested_id)
            wire_channel = "layer_a"

        rows.append(
            SkillChannelRow(
                requested_id=skill.requested_id,
                canonical_id=skill.canonical_id,
                origin=skill.origin,
                channel=wire_channel,
                disposition="delivered",
            )
        )

    return SkillPartition(
        layer_b=tuple(layer_b),
        layer_a=tuple(layer_a),
        layer_c=tuple(layer_c),
        provider_mount_slugs=mount_slugs,
        rows=tuple(rows),
    )


def caller_skill_ids_for_layer_c(
    effective: tuple[EffectiveSkill, ...],
    layer_c: tuple[str, ...],
) -> tuple[str, ...]:
    """Caller-origin ids routed to Channel C for ``resolve_injected_bodies``."""
    layer_c_set = frozenset(layer_c)
    return tuple(
        skill.requested_id
        for skill in effective
        if skill.origin == "caller" and skill.requested_id in layer_c_set
    )


def enrich_rows_with_inline_drops(
    rows: tuple[SkillChannelRow, ...],
    dropped: list[dict[str, object]],
) -> tuple[SkillChannelRow, ...]:
    """Mark Channel-C rows dropped when inline resolution excluded them."""
    drop_reasons: dict[str, str] = {}
    for item in dropped:
        entity_id = item.get("id")
        reason = item.get("reason")
        if not entity_id or not reason:
            continue
        drop_reasons[canonical_agent_skill_id(str(entity_id))] = str(reason)

    enriched: list[SkillChannelRow] = []
    for row in rows:
        reason = drop_reasons.get(row.canonical_id)
        if reason and row.channel == "layer_c":
            enriched.append(
                SkillChannelRow(
                    requested_id=row.requested_id,
                    canonical_id=row.canonical_id,
                    origin=row.origin,
                    channel=row.channel,
                    disposition="dropped",
                    drop_reason=reason,
                )
            )
        else:
            enriched.append(row)
    return tuple(enriched)
