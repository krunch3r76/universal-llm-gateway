"""Frontier-generate endpoint orchestration and persona admission checks."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from agent_seat import AgentMeta, assemble_system_prompt, hydrate_agent
from agent_seat.body_injection import (
    INJECTED_BODY_BUDGET_BYTES,
    RequiredBodyUnresolved,
    emit_layer_a_fs_line,
)
from agent_seat.inject_registry import (
    CallerSkillUnresolvedError,
    parse_packet_invariant_skill_ids,
    resolve_injected_bodies,
)
from agent_seat.role_entity_sync import resolve_dispatch_capabilities
from agent_seat.skills_merge import (
    McpPredicatedSkillsRejectedError,
    SkillsMountBackendInvalidError,
    caller_skill_ids_for_layer_c,
    enforce_mcp_predicated_skills,
    enrich_rows_with_inline_drops,
    partition_skill_channels,
    resolve_effective_skills,
)
from llm_adapters.capability_dispatch import project_knob_resolution
from model_capabilities import CapabilityCardError
from model_id import (
    ModelId,
    WireModelResolutionError,
    canonical_model_entity_id,
    resolve_wire_model_id,
)
from skills_mount import (
    SkillMountResolveError,
    resolve_skill_bundles,
    to_neutral_entries,
)
from skills_mount.resolve import default_workspaces_root

from .admission import (
    EventPublisher,
    FrontierEndpointError,
    _resolve_role_or_seat_profile,
    _translate_capability_card_error,
    assert_model_carded,
    emit_rejection,
    enforce_model,
    enforce_options,
    enforce_team_dispatch_generate_admit,
    is_chat_completions_only,
    mcp_enabled_for_frontier_dispatch,
    mcp_enabled_for_team_dispatch,
    verify_thread_writable,
)
from .anthropic_override_gate import enforce_anthropic_override
from .corpus_inline import (
    CORPUS_BODY_BUDGET_BYTES,
    corpus_inline_gated,
    inline_corpus_for_packet,
)
from .dispatch_messages import extract_last_user_message, wire_latest_user_turn
from .events import (
    DispatchSkillsChannelResolved,
    DispatchSkillsInlineRejected,
    DispatchSkillsInlineResolved,
    DispatchSkillsMounted,
    DispatchSkillsPredicatedRejected,
    DispatchSkillsPredicatedSkipped,
    FrontierEndpointPersonaResolved,
    FrontierEndpointRequested,
    PipelineFrontierDispatchCorpusInlined,
    PipelineFrontierDispatchCorpusUnresolved,
)
from .handoff import _resolve_packet_file, _workspaces_root

# renamed from frontier-dispatch (F-A, 2026-07-10)
_CHAT_DISPATCH_PIPELINE_ID = "chat-dispatch"
_TEAM_DISPATCH_PIPELINE_ID = "team-dispatch"


def _code_touching_generate(req: FrontierGenerateRequest) -> bool:
    return (
        req.role == "cursor-sdk"
        or (req.resolved_contract or "") == "implement"
        or bool(req.generation_options and req.generation_options.get("coding_session"))
    )


@dataclass(slots=True)
class FrontierGenerateRequest:
    messages: list[dict[str, Any]]
    model: str | None = None
    role: str | None = None
    system: str = ""
    mcp: bool | None = None
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    dispatch_thread_id: str | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = None
    # dispatch-surface-split Phase 1: explicit op discrimination
    output_contract: Literal["inline", "thread"] = "inline"
    target_thread: str | None = None
    op: Literal["generate", "to_thread"] | None = None
    # On-behalf delivery (2026-05-22) — caller-supplied subject for the
    # reply turn posted by Stargate. None ⇒ delivery handler auto-derives.
    reply_subject: str | None = None
    # Override post-delivery thread close for ``op="to_thread"``. None ⇒ ephemeral.
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None
    resolved_contract: str | None = None
    density_triage: str | None = None
    review_opt_out_reason_code: str | None = None
    auto_review_child: bool = False
    packet_path: str | None = None
    skills: list[str] | None = None
    server_tools: bool | None = None
    cost_intent: Literal["deliberate_high_cost"] | None = None
    suppress_cost_warning: bool = False
    cost_intent_reason: str | None = None
    spawn_review_provenance: Literal["generate_review_child"] | None = None


def _resolve_pre_hydration_effective_model(
    req: FrontierGenerateRequest,
    *,
    request_id: str,
) -> str:
    """Effective model for card gate — mirrors ``hydrate_agent`` model resolution."""
    raw: str | None = req.model
    if raw is None and req.role:
        from agent_seat.registry import resolve_agent_model

        try:
            raw = resolve_agent_model(req.role)
        except ValueError as exc:
            raise FrontierEndpointError(
                request_id=request_id,
                field="role",
                reason=str(exc),
                status_code=422,
            ) from exc
    if not raw:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="model is required when no role default is configured",
        )
    if ModelId.parse(raw).backend_type == "cursor_sdk":
        return raw
    try:
        return resolve_wire_model_id(raw, require_cloud=True).wire_id
    except WireModelResolutionError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason=str(exc),
        ) from exc


def _resolve_skills_mount(
    mount_skill_ids: tuple[str, ...],
    *,
    request_id: str,
    effective_model: str,
    role: str | None,
    event_publisher: EventPublisher | None,
) -> tuple[list[dict[str, Any]], frozenset[str], int]:
    if not mount_skill_ids:
        return [], frozenset(), 0
    try:
        bundles = resolve_skill_bundles(
            list(mount_skill_ids),
            workspaces_root=default_workspaces_root(),
        )
    except SkillMountResolveError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="skills",
            reason=str(exc),
        ) from exc
    neutral = to_neutral_entries(bundles)
    mount_slugs = frozenset(bundle.canonical_slug for bundle in bundles)
    total_bytes = sum(len(entry["data_base64"]) for entry in neutral)
    if event_publisher is not None:
        event_publisher(
            DispatchSkillsMounted(
                request_id=request_id,
                role=role,
                model=effective_model,
                canonical_slugs=sorted(mount_slugs),
                entry_count=len(bundles),
                total_bundle_bytes=total_bytes,
            )
        )
    return neutral, mount_slugs, total_bytes


def _emit_skills_channel_resolved(
    *,
    request_id: str,
    role: str | None,
    model: str,
    rows: tuple[Any, ...],
    event_publisher: EventPublisher | None,
) -> None:
    if not rows or event_publisher is None:
        return
    event_publisher(
        DispatchSkillsChannelResolved(
            request_id=request_id,
            role=role,
            model=model,
            skills=[
                {
                    "requested_id": row.requested_id,
                    "canonical_id": row.canonical_id,
                    "origin": row.origin,
                    "channel": row.channel,
                    "disposition": row.disposition,
                    **({"drop_reason": row.drop_reason} if row.drop_reason else {}),
                }
                for row in rows
            ],
        )
    )


def _emit_predicated_skipped(
    *,
    request_id: str,
    role: str | None,
    model: str,
    skip_rows: tuple[Any, ...],
    dropped: list[dict[str, object]] | None,
    event_publisher: EventPublisher | None,
) -> None:
    if event_publisher is None:
        return
    skipped: list[str] = [row.requested_id for row in skip_rows]
    for item in dropped or []:
        if item.get("reason") == "mcp_predicated_skip":
            entity_id = item.get("id")
            if entity_id:
                from agent_seat.guidance_entity import entity_slug_from_id

                skipped.append(entity_slug_from_id(str(entity_id)))
    if not skipped:
        return
    event_publisher(
        DispatchSkillsPredicatedSkipped(
            request_id=request_id,
            role=role,
            model=model,
            skills=sorted(set(skipped)),
        )
    )


def _classification_missing_slug(exc: Exception) -> str:
    import re

    match = re.search(r"canonical slug '([^']+)'", str(exc))
    return match.group(1) if match else "unknown"


def _dropped_entry_canonical(entry: dict[str, Any]) -> str:
    from implement_admission.skill_catalog_resolver import canonical_agent_skill_id

    slug = entry.get("slug")
    if slug:
        return canonical_agent_skill_id(str(slug))
    entity_id = entry.get("id")
    if entity_id:
        return canonical_agent_skill_id(str(entity_id))
    return ""


def _caller_origin_overflow_skills(
    dropped: list[dict[str, Any]],
    caller_canonical_set: frozenset[str],
) -> list[str]:
    skills: list[str] = []
    for entry in dropped:
        if entry.get("reason") != "layer_c_budget":
            continue
        canonical = _dropped_entry_canonical(entry)
        if canonical in caller_canonical_set:
            slug = entry.get("slug")
            if slug:
                skills.append(str(slug))
            elif canonical:
                from agent_seat.guidance_entity import entity_slug_from_id

                skills.append(entity_slug_from_id(canonical))
    return skills


def _classify_required_body_overflow(
    dropped: list[dict[str, Any]],
    caller_canonical_set: frozenset[str],
    *,
    request_id: str,
    role: str | None,
    model: str,
    event_publisher: EventPublisher | None = None,
) -> FrontierEndpointError | None:
    overflow_skills = _caller_origin_overflow_skills(dropped, caller_canonical_set)
    if not overflow_skills:
        return None
    if event_publisher is not None:
        event_publisher(
            DispatchSkillsInlineRejected(
                request_id=request_id,
                role=role,
                model=model,
                skills=overflow_skills,
                budget_bytes=INJECTED_BODY_BUDGET_BYTES,
                reason_code="budget",
            )
        )
    return FrontierEndpointError(
        request_id=request_id,
        field="skills",
        reason=(
            "caller Layer-C skill(s) exceed the inline injection budget: "
            + ", ".join(overflow_skills)
        ),
        status_code=422,
        code="skills_inline_budget_exceeded",
        details={
            "skills": overflow_skills,
            "budget_bytes": INJECTED_BODY_BUDGET_BYTES,
            "reason_code": "budget",
        },
    )


def _caller_canonical_set(caller_layer_c_ids: tuple[str, ...]) -> frozenset[str]:
    from implement_admission.skill_catalog_resolver import canonical_agent_skill_id

    return frozenset(
        canonical_agent_skill_id(skill_id) for skill_id in caller_layer_c_ids
    )


def _required_body_infra_error(request_id: str) -> FrontierEndpointError:
    return FrontierEndpointError(
        request_id=request_id,
        field="injected_bodies",
        reason="required conduct rule body failed to resolve",
    )


def _raise_required_body_unresolved(
    dropped: list[dict[str, Any]],
    caller_canonical_set: frozenset[str],
    *,
    request_id: str,
    role: str | None,
    model: str,
    event_publisher: EventPublisher | None = None,
) -> None:
    overflow_err = _classify_required_body_overflow(
        dropped,
        caller_canonical_set,
        request_id=request_id,
        role=role,
        model=model,
        event_publisher=event_publisher,
    )
    if overflow_err is not None:
        raise overflow_err
    raise _required_body_infra_error(request_id)


def _resolve_platform_for_generate(req: FrontierGenerateRequest) -> str:
    if not req.role:
        return "*"
    from agent_seat.registry import load_roles, normalize_agent_slug

    canonical = normalize_agent_slug(req.role)
    roles = load_roles()
    if canonical in roles:
        return roles[canonical].default_platform
    parts = canonical.split("-", 1)
    if len(parts) == 2:
        return parts[1]
    return "*"


def _packet_text_for_invariants(
    req: FrontierGenerateRequest,
) -> str:
    if req.packet_path:
        packet_file = _resolve_packet_file(
            _workspaces_root().resolve(), req.packet_path
        )
        if packet_file is not None:
            return packet_file.read_text(encoding="utf-8", errors="replace")
    for message in reversed(req.messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return ""


def _inject_profile_for_generate(req: FrontierGenerateRequest) -> str | None:
    if (req.resolved_contract or "") == "implement":
        return "dispatch"
    return None


async def build_dispatch_body(
    req: FrontierGenerateRequest, event_publisher: EventPublisher | None = None
) -> dict[str, Any]:
    """Apply role rules and shape dispatch JSON for ``/pipelines/dispatch``.

    Phase 5: ``req.role`` selects a ``role:{slug}`` execution contract loaded
    by ``hydrate_agent`` (which fetches the role: entity per the updated
    ``_SELF_ENTITY`` map). The internal ``agent=`` keyword to event payloads
    and to ``hydrate_agent`` retains its historical name (event observability
    schema preservation); only the public dispatch parameter is renamed.
    """
    request_id = uuid.uuid4().hex[:12]
    if event_publisher is not None:
        event_publisher(
            FrontierEndpointRequested(
                request_id=request_id,
                agent=req.role,
                model=req.model,
            )
        )

    if req.role:
        enforce_team_dispatch_generate_admit(
            req.role,
            request_id=request_id,
            event_publisher=event_publisher,
            caller_agent=req.caller_agent,
        )

    effective_model = _resolve_pre_hydration_effective_model(
        req, request_id=request_id
    )
    # CC-only models have no Responses-native capability card; they route via
    # chat-dispatch respond_cc (generate → /v1/chat/completions). Role-carrying
    # rejects immediately (persona-free envelope only). Role-less skips the
    # card gate so admission reaches the generate branch.
    cc_only = is_chat_completions_only(effective_model)
    if req.role is not None and cc_only:
        raise emit_rejection(
            request_id=request_id,
            agent=req.role,
            field="model",
            reason=(
                f"{effective_model!r} is a Chat Completions-only model — "
                "unavailable on the OpenAI Responses API used by "
                "role-carrying team_dispatch. Dispatch role-less via "
                "chat-dispatch (pipeline_options.model) for the Chat "
                "Completions branch, or choose a Responses-capable model."
            ),
            event_publisher=event_publisher,
        )
    if not cc_only:
        assert_model_carded(
            effective_model,
            request_id=request_id,
            event_publisher=event_publisher,
        )

    if req.role and req.model is not None:
        _to, _family, _platform, profile = _resolve_role_or_seat_profile(
            req.role, request_id=request_id
        )
        enforce_anthropic_override(
            request_id=request_id,
            model=req.model,
            profile_provider=profile.provider,
            profile_allowed_models=profile.allowed_models,
            cost_intent=req.cost_intent,
            cost_intent_reason=req.cost_intent_reason,
            spawn_review_provenance=req.spawn_review_provenance,
        )

    inject_profile = _inject_profile_for_generate(req)
    code_touching = _code_touching_generate(req)
    packet_invariant_ids = parse_packet_invariant_skill_ids(
        _packet_text_for_invariants(req)
    )
    platform = _resolve_platform_for_generate(req)

    if cc_only:
        # Generate-branch envelope is inline one-shot (no MCP tool loop).
        mcp_enabled = False
    elif req.role is not None:
        mcp_enabled = mcp_enabled_for_team_dispatch(
            effective_model,
            req.mcp,
            request_id=request_id,
            event_publisher=event_publisher,
        )
    else:
        mcp_enabled = mcp_enabled_for_frontier_dispatch(
            effective_model,
            req.mcp,
            request_id=request_id,
            event_publisher=event_publisher,
        )

    effective_skills = resolve_effective_skills(
        req.skills,
        role=req.role,
        platform=platform,
        inject_profile=inject_profile,
        code_touching=code_touching,
        packet_invariant_ids=packet_invariant_ids,
    )
    predicated_skip_rows: tuple[Any, ...] = ()
    try:
        effective_skills, predicated_skip_rows = enforce_mcp_predicated_skills(
            effective_skills,
            mcp_enabled=mcp_enabled,
        )
    except McpPredicatedSkillsRejectedError as exc:
        if event_publisher is not None:
            event_publisher(
                DispatchSkillsPredicatedRejected(
                    request_id=request_id,
                    role=req.role,
                    model=effective_model,
                    skills=list(exc.skills),
                    origin="caller",
                )
            )
        raise FrontierEndpointError(
            request_id=request_id,
            field="skills",
            reason=(
                "MCP-predicated caller skills cannot be dispatched to a non-MCP context"
            ),
            status_code=422,
            code="skills_mcp_predicated",
            details={
                "model": effective_model,
                "skills": list(exc.skills),
                "reason_code": "skills_mcp_predicated",
            },
        ) from exc
    except LookupError as exc:
        from implement_admission.skill_mcp_classification import (
            SkillClassificationMissingError,
        )

        if isinstance(exc, SkillClassificationMissingError):
            slug = _classification_missing_slug(exc)
            raise FrontierEndpointError(
                request_id=request_id,
                field="skills",
                reason=str(exc),
                status_code=422,
                code="skill_classification_missing",
                details={
                    "slug": slug,
                    "reason_code": "skill_classification_missing",
                },
            ) from exc
        raise
    skill_partition = None
    channel_rows: tuple[Any, ...] = predicated_skip_rows
    provider_mount_slugs: frozenset[str] = frozenset()
    caller_layer_c_ids: tuple[str, ...] = ()
    cursor_sdk_model = ModelId.parse(effective_model).backend_type == "cursor_sdk"
    # CC-only generate branch is persona-free / skills-free (chat-dispatch respond_cc envelope).
    # Skip partition — uncarded search-api models have no skills_mount_backend.
    if effective_skills and not cursor_sdk_model and not cc_only:
        try:
            skill_partition = partition_skill_channels(
                effective_skills,
                model=effective_model,
                mcp_enabled=mcp_enabled,
                role=req.role,
                platform=platform,
                inject_profile=inject_profile,
                code_touching=code_touching,
            )
        except CapabilityCardError as exc:
            raise _translate_capability_card_error(
                exc,
                request_id=request_id,
                event_publisher=event_publisher,
            ) from exc
        except SkillsMountBackendInvalidError as exc:
            raise FrontierEndpointError(
                request_id=request_id,
                field="model",
                reason=str(exc),
                status_code=422,
                code="capability_card_value_invalid",
                details={
                    "model": exc.model,
                    "capability_field": "skills_mount_backend",
                    "value": exc.value,
                    "reason_code": "capability_card_value_invalid",
                },
            ) from exc
        channel_rows = skill_partition.rows
        provider_mount_slugs = skill_partition.provider_mount_slugs
        caller_layer_c_ids = caller_skill_ids_for_layer_c(
            effective_skills, skill_partition.layer_c
        )
        channel_rows = (*predicated_skip_rows, *channel_rows)

    layer_c_caller_canonical = _caller_canonical_set(caller_layer_c_ids)

    meta = AgentMeta()
    system_assembled = req.system or ""
    skills_mount: list[dict[str, Any]] | None = None
    if skill_partition is not None and skill_partition.layer_b:
        skills_mount, provider_mount_slugs, _total = _resolve_skills_mount(
            skill_partition.layer_b,
            request_id=request_id,
            effective_model=effective_model,
            role=req.role,
            event_publisher=event_publisher,
        )

    if req.role:
        # Soft boot: team_dispatch and persona-free frontier HTTP dispatches use the
        # lightweight profile by default. Drops deadlines + review-queue
        # fetches; keeps a 3-reflection floor. The pipeline-handler hydration
        # in resolve_dispatch_tool_set must mirror this profile to avoid the
        # final dispatched prompt regaining a heavy briefing card.
        try:
            bundle = await hydrate_agent(
                req.role,
                fetch_profile="light",
                model=req.model,
                inject_profile=inject_profile,
                code_touching=code_touching,
                packet_invariant_ids=packet_invariant_ids,
                caller_skill_ids=caller_layer_c_ids,
                provider_mount_slugs=provider_mount_slugs,
                exclude_mcp_predicated=not mcp_enabled,
            )
        except CallerSkillUnresolvedError as exc:
            raise FrontierEndpointError(
                request_id=request_id,
                field="skills",
                reason=str(exc),
                status_code=422,
            ) from exc
        except LookupError as exc:
            from implement_admission.skill_mcp_classification import (
                SkillClassificationMissingError,
            )

            if isinstance(exc, SkillClassificationMissingError):
                slug = _classification_missing_slug(exc)
                raise FrontierEndpointError(
                    request_id=request_id,
                    field="skills",
                    reason=str(exc),
                    status_code=422,
                    code="skill_classification_missing",
                    details={
                        "slug": slug,
                        "reason_code": "skill_classification_missing",
                    },
                ) from exc
            raise
        # Layer-C body-inject for no-fs generate roles (e.g. openai/gpt-5.5) is enforced
        # inside hydrate_agent → resolve_injected_bodies(inline_only_dispatch=True).
        meta = bundle.agent_meta
        if event_publisher is not None:
            event_publisher(
                FrontierEndpointPersonaResolved(
                    request_id=request_id,
                    agent=req.role,
                    frontier_kind=meta.frontier_kind,
                    default_model=meta.default_model,
                    allowed_models_count=len(meta.allowed_models),
                    allowed_options_count=(
                        len(meta.allowed_options)
                        if meta.allowed_options is not None
                        else None
                    ),
                )
            )
        if bundle.required_body_unresolved:
            _raise_required_body_unresolved(
                bundle.required_body_dropped or [],
                layer_c_caller_canonical,
                request_id=request_id,
                role=req.role,
                model=effective_model,
                event_publisher=event_publisher,
            )
        injected_bodies_md = bundle.injected_bodies_md
        system_assembled = assemble_system_prompt(
            req.role,
            briefing_card_md=bundle.briefing_card_md,
            continuation_md=bundle.continuation_md,
            extra_system=req.system,
            inline_only=bundle.inline_only,
            injected_bodies_md=injected_bodies_md,
        )
        if skill_partition is not None and skill_partition.layer_a:
            layer_a_block = "".join(
                emit_layer_a_fs_line(skill_id)
                for skill_id in skill_partition.layer_a
            )
            if layer_a_block:
                system_assembled = f"{system_assembled}{layer_a_block}"
        if bundle.injection_meta:
            channel_rows = enrich_rows_with_inline_drops(
                channel_rows,
                bundle.injection_meta.get("dropped") or [],
            )
        if event_publisher is not None and (
            bundle.inline_only or bundle.injection_meta
        ):
            meta_inj = bundle.injection_meta or {}
            metrics = meta_inj.get("metrics") or {}
            injected = meta_inj.get("injected") or []
            event_publisher(
                DispatchSkillsInlineResolved(
                    request_id=request_id,
                    seat=req.role,
                    model=req.model or meta.default_model,
                    injected=injected,
                    dropped=meta_inj.get("dropped") or [],
                    total_bytes=sum(
                        int(item.get("bytes", 0))
                        for item in injected
                        if isinstance(item, dict)
                    ),
                    budget_bytes=INJECTED_BODY_BUDGET_BYTES,
                    cache_hit=bool(metrics.get("cache_hit")),
                    cold_fetches=int(metrics.get("cold_fetches", 0)),
                    elapsed_ms=int(metrics.get("elapsed_ms", 0)),
                    deadline_hit=bool(metrics.get("deadline_hit")),
                )
            )
        _emit_predicated_skipped(
            request_id=request_id,
            role=req.role,
            model=effective_model,
            skip_rows=predicated_skip_rows,
            dropped=(
                (bundle.injection_meta or {}).get("dropped")
                if bundle.injection_meta
                else None
            ),
            event_publisher=event_publisher,
        )

    elif skill_partition is not None:
        try:
            resolution = resolve_injected_bodies(
                "",
                role=None,
                platform=platform,
                inject_profile=inject_profile,
                code_touching=code_touching,
                packet_invariant_ids=packet_invariant_ids,
                caller_skill_ids=caller_layer_c_ids,
                provider_mount_slugs=provider_mount_slugs,
                exclude_mcp_predicated=not mcp_enabled,
            )
        except CallerSkillUnresolvedError as exc:
            raise FrontierEndpointError(
                request_id=request_id,
                field="skills",
                reason=str(exc),
                status_code=422,
            ) from exc
        except LookupError as exc:
            from implement_admission.skill_mcp_classification import (
                SkillClassificationMissingError,
            )

            if isinstance(exc, SkillClassificationMissingError):
                slug = _classification_missing_slug(exc)
                raise FrontierEndpointError(
                    request_id=request_id,
                    field="skills",
                    reason=str(exc),
                    status_code=422,
                    code="skill_classification_missing",
                    details={
                        "slug": slug,
                        "reason_code": "skill_classification_missing",
                    },
                ) from exc
            raise
        except RequiredBodyUnresolved as exc:
            _raise_required_body_unresolved(
                exc.dropped,
                layer_c_caller_canonical,
                request_id=request_id,
                role=req.role,
                model=effective_model,
                event_publisher=event_publisher,
            )
        if resolution.block_md:
            system_assembled = f"{system_assembled}{resolution.block_md}"
        if skill_partition.layer_a:
            layer_a_block = "".join(
                emit_layer_a_fs_line(skill_id)
                for skill_id in skill_partition.layer_a
            )
            if layer_a_block:
                system_assembled = f"{system_assembled}{layer_a_block}"
        channel_rows = enrich_rows_with_inline_drops(
            channel_rows,
            resolution.dropped,
        )
        if event_publisher is not None and (
            resolution.injected or resolution.dropped
        ):
            event_publisher(
                DispatchSkillsInlineResolved(
                    request_id=request_id,
                    seat="",
                    model=req.model or effective_model,
                    injected=resolution.injected,
                    dropped=resolution.dropped,
                    total_bytes=sum(
                        int(item.get("bytes", 0))
                        for item in resolution.injected
                        if isinstance(item, dict)
                    ),
                    budget_bytes=INJECTED_BODY_BUDGET_BYTES,
                    cache_hit=bool(resolution.telemetry.get("cache_hit")),
                    cold_fetches=int(resolution.telemetry.get("cold_fetches", 0)),
                    elapsed_ms=0,
                    deadline_hit=False,
                )
            )
        _emit_predicated_skipped(
            request_id=request_id,
            role=req.role,
            model=effective_model,
            skip_rows=predicated_skip_rows,
            dropped=resolution.dropped,
            event_publisher=event_publisher,
        )

    if not cc_only and corpus_inline_gated(effective_model):
        corpus_result = inline_corpus_for_packet(
            _packet_text_for_invariants(req),
            budget_bytes=CORPUS_BODY_BUDGET_BYTES,
            workspaces_root_override=_workspaces_root(),
            already_present=system_assembled,
        )
        if corpus_result.block_md:
            system_assembled = f"{system_assembled}{corpus_result.block_md}"
        if event_publisher is not None:
            if corpus_result.injected or corpus_result.dropped:
                event_publisher(
                    PipelineFrontierDispatchCorpusInlined(
                        request_id=request_id,
                        role=req.role,
                        model=effective_model,
                        injected_count=len(corpus_result.injected),
                        dropped_count=len(corpus_result.dropped),
                        injected_bytes=corpus_result.injected_bytes,
                        dropped_bytes=corpus_result.dropped_bytes,
                        budget_bytes=CORPUS_BODY_BUDGET_BYTES,
                    )
                )
            for uri in corpus_result.unresolved:
                event_publisher(
                    PipelineFrontierDispatchCorpusUnresolved(
                        request_id=request_id,
                        role=req.role,
                        model=effective_model,
                        uri=uri,
                    )
                )

    _emit_skills_channel_resolved(
        request_id=request_id,
        role=req.role,
        model=effective_model,
        rows=channel_rows,
        event_publisher=event_publisher,
    )

    model_entity_id = canonical_model_entity_id(effective_model)
    enforce_model(
        request_id=request_id,
        agent=req.role,
        model=effective_model,
        meta=meta,
        explicit_model=req.model is not None,
        event_publisher=event_publisher,
    )
    generation_options = dict(req.generation_options or {})
    if req.reasoning_effort is not None:
        generation_options.setdefault("reasoning_effort", req.reasoning_effort)
    if "max_tool_turns" in generation_options:
        raise FrontierEndpointError(
            request_id=request_id,
            field="generation_options.max_tool_turns",
            reason=(
                "'max_tool_turns' inside generation_options is not supported — "
                "use the typed top-level parameter instead"
            ),
        )
    enforce_options(
        request_id=request_id,
        agent=req.role,
        opts=generation_options,
        meta=meta,
        event_publisher=event_publisher,
    )
    _eff = generation_options.get("reasoning_effort")
    _eff = _eff if isinstance(_eff, str) and _eff else None
    _maxt = generation_options.get("max_tokens")
    _knob_resolution_preview = project_knob_resolution(
        resolved_model=effective_model,
        requested_effort=_eff,
        requested_max_output=_maxt if isinstance(_maxt, int) else None,
    )

    capability_preview: dict[str, Any] | None = None
    if req.role is not None:
        # Echo single-sourced with the pipeline gate: passing the effective
        # ``mcp_enabled`` (post caller-knob) means the transparency surface and
        # ``pipeline_options["mcp"]`` derive from one value and cannot drift.
        capability_preview = resolve_dispatch_capabilities(
            model=effective_model, mcp_enabled=mcp_enabled
        )
        capability_preview["role"] = req.role

    pipeline_options: dict[str, Any] = {
        "model": effective_model,
        "model_entity_id": model_entity_id,
        "system": system_assembled,
        "generation_parameters": generation_options,
        "mcp": mcp_enabled,
        "_endpoint_request_id": request_id,
        "_knob_resolution_preview": _knob_resolution_preview,
        "output_contract": req.output_contract,
    }
    if capability_preview is not None:
        pipeline_options["_capability_preview"] = capability_preview
    if req.role:
        pipeline_options["role"] = req.role
    if req.max_tool_turns is not None:
        pipeline_options["max_tool_turns"] = req.max_tool_turns
    elif req.role is not None:
        from agent_seat.tool_loop_budget import API_DEFAULT_MAX_TOOL_TURNS

        # API native tool loop only — cursor-sdk does not consume this knob.
        pipeline_options["max_tool_turns"] = API_DEFAULT_MAX_TOOL_TURNS
    if skills_mount is not None:
        pipeline_options["skills_mount"] = skills_mount
    if req.server_tools is not None:
        pipeline_options["server_tools"] = req.server_tools

    if req.output_contract == "thread" and req.target_thread:
        _agent_bus_token = os.getenv("AGENT_BUS_TOKEN", "").strip()
        _allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if not _agent_bus_token and not _allow_unset:
            raise FrontierEndpointError(
                request_id=request_id,
                field="thread",
                reason=(
                    "AGENT_BUS_TOKEN is not configured; thread output contract "
                    "requires agent-bus verification before dispatch. "
                    "Set AGENT_BUS_TOKEN in the Stargate environment, or "
                    "ALLOW_UNSET_AGENT_BUS_TOKEN=true for explicit local bypass."
                ),
                status_code=503,
            )
        if _agent_bus_token:
            await verify_thread_writable(
                req.target_thread,
                request_id=request_id,
                auth_token=_agent_bus_token,
            )

    pipeline_id = (
        _TEAM_DISPATCH_PIPELINE_ID if req.role else _CHAT_DISPATCH_PIPELINE_ID
    )

    if req.role:
        dispatch_thread_id = (req.dispatch_thread_id or "").strip()
        if not dispatch_thread_id:
            raise FrontierEndpointError(
                request_id=request_id,
                field="dispatch_thread_id",
                reason=(
                    "dispatch_thread_id is required for team_dispatch — "
                    "binds server-owned assemble/archive on "
                    "thread:dispatch:{dispatch_thread_id}"
                ),
            )
        last_user = extract_last_user_message(req.messages)
        if not last_user:
            raise FrontierEndpointError(
                request_id=request_id,
                field="messages",
                reason="At least one non-empty user message is required",
            )
        wire_messages = wire_latest_user_turn(req.messages)
    else:
        dispatch_thread_id = None
        wire_messages = req.messages

    body: dict[str, Any] = {
        "model": pipeline_id,
        "messages": wire_messages,
        "pipeline_options": pipeline_options,
        "output_contract": req.output_contract,
    }
    if req.timeout_seconds is not None:
        body["timeout_seconds"] = req.timeout_seconds
    if req.caller_agent:
        body["caller_agent"] = req.caller_agent
    if dispatch_thread_id:
        body["dispatch_thread_id"] = dispatch_thread_id
    if req.transcript_id:
        body["caller_transcript_id"] = req.transcript_id
    if req.target_thread is not None:
        body["target_thread"] = req.target_thread
    if req.op is not None:
        body["op"] = req.op
    if req.resolved_contract is not None:
        body["resolved_contract"] = req.resolved_contract
    if req.op == "to_thread":
        if req.role:
            body["from_agent"] = req.role
        else:
            model_short = effective_model.replace("/", ":")
            body["from_agent"] = f"frontier:{model_short}"
    if req.reply_subject is not None:
        body["reply_subject"] = req.reply_subject
    if req.bus_lifecycle is not None:
        body["bus_lifecycle"] = req.bus_lifecycle
    return body
