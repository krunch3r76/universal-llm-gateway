"""Read-only normalizer: source_ref → ImplementSpec."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from implement_admission.admission_read import read_packet
from implement_admission.deck_resolver import NormalizedDeck, resolve_phase_deck
from implement_admission.routing import (
    classify_risk_tier,
    derive_routing,
    normalize_author_family,
)
from implement_admission.source_ref import SourceRef, SourceRefError, parse_source_ref
from implement_admission.spec import (
    Acceptance,
    Closeout,
    CloseoutAdapterKind,
    ImplementSpec,
    Intent,
    Readiness,
    ReadinessState,
    ReviewAttestation,
    Scope,
    Source,
    SourceKind,
    SourceVersion,
    finalize_spec,
)


class CortexReader(Protocol):
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


def normalize(
    raw_source_ref: str,
    *,
    cortex: CortexReader,
    workspaces_root: Any = None,
    dirty_tree_risk: bool = False,
    author_family: str | None = None,
    contract: str | None = None,
    seat: str | None = None,
    role: str | None = None,
    transport: str = "team_dispatch",
) -> ImplementSpec:
    """Parse, resolve (read-only), derive routing, and return ImplementSpec."""
    ref = parse_source_ref(raw_source_ref)

    # task: is provenance-only — it is not a materialisable dispatch source
    if ref.source_kind == SourceKind.TASK.value:
        raise SourceRefError(
            code="source_ref_not_dispatchable",
            source_ref=raw_source_ref,
            rule="task: refs are provenance-only; dispatch requires todo:|plan:|plan_phase:|packet:|agent-bus:",
        )

    now = datetime.now(UTC)
    route_kwargs = {
        "contract": contract,
        "seat": seat,
        "role": role,
        "transport": transport,
    }

    if ref.source_kind == SourceKind.AGENT_BUS.value:
        spec = _normalize_agent_bus(
            ref,
            cortex=cortex,
            now=now,
            dirty_tree_risk=dirty_tree_risk,
            route_kwargs=route_kwargs,
        )
    elif ref.source_kind == SourceKind.PACKET.value:
        spec = _normalize_packet(
            ref,
            workspaces_root=workspaces_root,
            now=now,
            dirty_tree_risk=dirty_tree_risk,
            route_kwargs=route_kwargs,
        )
    else:
        spec = _normalize_entity(
            ref,
            cortex=cortex,
            now=now,
            dirty_tree_risk=dirty_tree_risk,
            workspaces_root=workspaces_root,
            route_kwargs=route_kwargs,
        )
    return _stamp_review_attestation(spec, author_family)


def _stamp_review_attestation(
    spec: ImplementSpec,
    author_family: str | None,
) -> ImplementSpec:
    risk_tier = classify_risk_tier(spec)
    fam = normalize_author_family(author_family)
    att = ReviewAttestation(
        required=(risk_tier in {"material", "critical"} and fam == "claude"),
        risk_tier=risk_tier,
        author_family=fam,
        spec_hash=None,
        disposition="missing",
    )
    return spec.model_copy(
        update={
            "provenance": spec.provenance.model_copy(update={"review_attestation": att})
        }
    )


def _normalize_entity(
    ref: SourceRef,
    *,
    cortex: CortexReader,
    now: datetime,
    dirty_tree_risk: bool = False,
    workspaces_root: Any = None,
    route_kwargs: dict[str, Any] | None = None,
) -> ImplementSpec:
    entity_id = ref.canonical_ref
    try:
        entity = cortex.entity_get(entity_id, intent="full")
    except Exception as exc:
        raise SourceRefError(
            code="source_not_found",
            source_ref=ref.external_ref,
            rule=f"entity_get({entity_id!r})",
            message=str(exc),
        ) from exc

    if not entity or entity.get("id") is None:
        raise SourceRefError(
            code="source_not_found",
            source_ref=ref.external_ref,
            rule=f"entity_get({entity_id!r}) returned empty",
        )

    attrs = entity.get("attributes") or {}
    name = entity.get("name") or entity_id
    content_hash = attrs.get("content_hash") or entity.get("content_hash")

    kind = ref.source_kind
    multi_phase = False
    trips_threshold = False
    gated_reason: str | None = None

    if kind == SourceKind.PLAN.value:
        phases = attrs.get("phases") or 0
        if isinstance(phases, list):
            multi_phase = len(phases) > 1
        elif isinstance(phases, int):
            multi_phase = phases > 1
        if not multi_phase and not phases:
            gated_reason = "plan has no phases"

    deck: NormalizedDeck | None = None
    if kind == SourceKind.PLAN_PHASE.value:
        phase_dir = attrs.get("phase_dir")
        if phase_dir is None and not attrs.get("phase_number"):
            raise SourceRefError(
                code="phase_not_found",
                source_ref=ref.external_ref,
                rule=f"plan_phase entity {entity_id!r} missing phase metadata",
            )
        # Dispatch-bound lane (workspaces_root supplied) MUST resolve + read the
        # deck or hard-fail — never degrade to attr-only, which would recreate the
        # metadata-only packet this adapter exists to fix (spec §15 A1). The
        # attr-only path is reserved for non-dispatch callers (workspaces_root is
        # None), e.g. unit tests with no deck on disk.
        if workspaces_root is not None:
            deck = resolve_phase_deck(
                ref,
                workspaces_root=Path(workspaces_root),
                entity_attrs=attrs,
            )

    if kind == SourceKind.TODO.value:
        trips_threshold = bool(attrs.get("multi_phase_arc"))

    files_expected = _files_from_entity(attrs)
    entity_acs = _acceptance_from_entity(attrs, name)
    open_design = False
    description: str | None = None

    if deck is not None:
        files_expected = _dedupe_preserve([*files_expected, *deck.files_expected])
        if _entity_acs_defaulted(entity_acs, name) and deck.acceptance:
            acs = deck.acceptance
        else:
            acs = _dedupe_preserve([*entity_acs, *deck.acceptance])
        open_design = deck.open_design
        description = deck.objective
    else:
        acs = entity_acs

    has_dense = len(acs) >= 1
    has_files = len(files_expected) >= 1

    routing = None
    readiness_state = ReadinessState.READY

    if gated_reason:
        readiness_state = ReadinessState.GATED
    elif kind == SourceKind.TODO.value and trips_threshold:
        readiness_state = ReadinessState.GATED
        gated_reason = "todo trips Todo→Plan threshold — coordinator route required"
    else:
        routing = derive_routing(
            kind,
            multi_phase=multi_phase,
            trips_todo_plan_threshold=trips_threshold,
            has_complete_file_list=has_files,
            has_dense_acs=has_dense,
            open_design=open_design,
            dirty_tree_risk=dirty_tree_risk,
        )

    adapter = _adapter_for_kind(kind)
    raw_uri = entity.get("source_uri")
    source_uri: str | None = None
    if raw_uri is not None:
        stripped = str(raw_uri).strip()
        if stripped:
            source_uri = stripped.removeprefix("files://")
    source = Source(
        source_ref=ref.external_ref,
        canonical_ref=ref.canonical_ref,
        parent_ref=ref.parent_ref,
        selector=ref.selector,
        source_kind=SourceKind(kind),
        source_uri=source_uri,
        source_version=SourceVersion(
            content_hash=content_hash,
            deck_sha256=deck.sha256 if deck is not None else None,
        ),
    )

    spec = ImplementSpec(
        source=source,
        intent=Intent(summary=str(name), description=description),
        scope=Scope(
            files_expected=files_expected,
            bounded=True,
            deck_body=deck.body if deck is not None else None,
        ),
        readiness=Readiness(
            state=readiness_state,
            gated_reason=gated_reason,
            freshness_checked_at=now,
        ),
        skills=list(attrs.get("required_skills") or []),
        routing=routing,
        acceptance=Acceptance(criteria=acs or [f"Complete work for {entity_id}"]),
        closeout=Closeout(adapter=adapter),
    )
    return finalize_spec(spec, **(route_kwargs or {}))


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _entity_acs_defaulted(acs: list[str], name: str) -> bool:
    return acs == [f"Complete {name}"]


def infer_packet_legacy_route(text: str) -> dict[str, str]:
    """Independent oracle for shadow replay — encodes expected legacy_route from packet body."""
    if not _packet_has_acceptance(text):
        return {"expect_error": "handoff_packet_missing_acceptance"}
    has_files, has_dense_acs = _packet_routing_flags(text)
    routing = derive_routing(
        SourceKind.PACKET.value,
        packet_shape="single",
        has_complete_file_list=has_files,
        has_dense_acs=has_dense_acs,
    )
    if routing is None:
        return {"expect_error": "handoff_packet_missing_acceptance"}
    return {
        "orchestration_mode": routing.orchestration_mode.value,
        "executor_style": routing.executor_style.value,
    }


def _normalize_packet(
    ref: SourceRef,
    *,
    workspaces_root: Any,
    now: datetime,
    dirty_tree_risk: bool = False,
    route_kwargs: dict[str, Any] | None = None,
) -> ImplementSpec:
    path = ref.external_ref.split(":", 1)[1]
    try:
        packet = read_packet(path, workspaces_root=workspaces_root)
    except SourceRefError:
        raise
    except Exception as exc:
        raise SourceRefError(
            code="handoff_packet_missing",
            source_ref=ref.external_ref,
            rule="read_packet",
            message=str(exc),
        ) from exc

    if not _packet_has_acceptance(packet.text):
        raise SourceRefError(
            code="handoff_packet_missing_acceptance",
            source_ref=ref.external_ref,
            rule="acceptance in task_guidance, output_format, or acceptance/success-criteria headings",
        )

    files_expected = _files_from_packet(packet.text)
    has_files, has_dense_acs = _packet_routing_flags(packet.text)
    routing = derive_routing(
        SourceKind.PACKET.value,
        packet_shape="single",
        has_complete_file_list=has_files,
        has_dense_acs=has_dense_acs,
        dirty_tree_risk=dirty_tree_risk,
    )

    source = Source(
        source_ref=ref.external_ref,
        canonical_ref=ref.canonical_ref,
        parent_ref=None,
        selector=None,
        source_kind=SourceKind.PACKET,
        source_version=SourceVersion(packet_sha256=packet.packet_sha256),
    )

    spec = ImplementSpec(
        source=source,
        intent=Intent(summary=f"Packet {path}"),
        scope=Scope(files_expected=files_expected, bounded=True),
        readiness=Readiness(state=ReadinessState.READY, freshness_checked_at=now),
        routing=routing,
        acceptance=Acceptance(criteria=_acceptance_from_packet(packet.text)),
        closeout=Closeout(adapter=CloseoutAdapterKind.PACKET),
    )
    return finalize_spec(spec, **(route_kwargs or {}))


def _normalize_agent_bus(
    ref: SourceRef,
    *,
    cortex: CortexReader,
    now: datetime,
    dirty_tree_risk: bool = False,
    route_kwargs: dict[str, Any] | None = None,
) -> ImplementSpec:
    ambiguous = ref.turn is None
    gated_reason = (
        "agent-bus thread ambiguous — explicit #turn-N or linked source required"
    )

    source = Source(
        source_ref=ref.external_ref,
        canonical_ref=ref.canonical_ref,
        parent_ref=None,
        selector=None,
        source_kind=SourceKind.AGENT_BUS,
        source_version=SourceVersion(),
    )

    if not ambiguous:
        routing = derive_routing(
            SourceKind.AGENT_BUS.value,
            ambiguous_bus=False,
            has_dense_acs=True,
            dirty_tree_risk=dirty_tree_risk,
        )
        spec = ImplementSpec(
            source=source,
            intent=Intent(summary=f"Agent-bus turn {ref.turn}"),
            readiness=Readiness(state=ReadinessState.READY, freshness_checked_at=now),
            routing=routing,
            acceptance=Acceptance(criteria=["Execute implement intent from bus turn"]),
            closeout=Closeout(
                adapter=CloseoutAdapterKind.AGENT_BUS,
                bus_thread=ref.canonical_ref.split("#")[0],
            ),
        )
        return finalize_spec(spec, **(route_kwargs or {}))

    spec = ImplementSpec(
        source=source,
        intent=Intent(summary="Agent-bus implement intent (ambiguous)"),
        readiness=Readiness(
            state=ReadinessState.GATED,
            gated_reason=gated_reason,
            freshness_checked_at=now,
        ),
        routing=None,
        acceptance=Acceptance(criteria=["Resolve ambiguity before implement"]),
        closeout=Closeout(
            adapter=CloseoutAdapterKind.AGENT_BUS,
            bus_thread=ref.canonical_ref,
        ),
    )
    return finalize_spec(spec, **(route_kwargs or {}))


def _adapter_for_kind(kind: str) -> CloseoutAdapterKind:
    mapping = {
        SourceKind.TODO.value: CloseoutAdapterKind.TODO,
        SourceKind.PLAN.value: CloseoutAdapterKind.PLAN,
        SourceKind.PLAN_PHASE.value: CloseoutAdapterKind.PLAN_PHASE,
        SourceKind.PACKET.value: CloseoutAdapterKind.PACKET,
        SourceKind.AGENT_BUS.value: CloseoutAdapterKind.AGENT_BUS,
        # SourceKind.TASK is deliberately absent: task: refs are provenance-only
        # and are rejected by the normalize() guard before this mapping is reached.
        # Do not add a TASK → CloseoutAdapterKind entry here.
    }
    return mapping.get(kind, CloseoutAdapterKind.MIXED)


def _extract_block(text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)
    return match.group(1) if match else None


_ACCEPTANCE_HEADING = re.compile(
    r"^#{1,3}\s+(?:acceptance(?:\s+criteria)?|success criteria)\b",
    re.IGNORECASE | re.MULTILINE,
)
_ACCEPTANCE_PHRASE = re.compile(r"\bacceptance criteria\b", re.IGNORECASE)
_FILE_PATH_SUFFIXES = (".py", ".md", ".yaml", ".yml", ".json", ".toml", ".mdc")
_MAX_FILE_PATH_LEN = 200
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _packet_has_acceptance(text: str) -> bool:
    guidance = _extract_block(text, "task_guidance") or ""
    if "acceptance" in guidance.lower():
        return True
    output_fmt = _extract_block(text, "output_format") or ""
    if "acceptance" in output_fmt.lower():
        return True
    if _ACCEPTANCE_HEADING.search(text):
        return True
    return _ACCEPTANCE_PHRASE.search(text) is not None


def _looks_like_file_path(path: str) -> bool:
    candidate = path.strip()
    # Whitespace/overlength reject non-path captures (e.g. multi-line code-fence
    # bodies the inline-backtick scan over-captures). Without this, slash-bearing
    # prose like "worker/coord" inside a docstring becomes a bogus files_expected
    # entry and crashes closeout with OSError ENAMETOOLONG (errno 36).
    if not candidate or len(candidate) > _MAX_FILE_PATH_LEN:
        return False
    if any(ch.isspace() for ch in candidate):
        return False
    return "/" in candidate or candidate.endswith(_FILE_PATH_SUFFIXES)


def _strip_fenced_blocks(text: str) -> str:
    return _FENCE_RE.sub(" ", text)


def _files_from_packet(text: str) -> list[str]:
    scope_text = _extract_block(text, "scope") or ""
    seen: set[str] = set()
    files: list[str] = []
    for candidate in _files_from_scope(scope_text):
        if candidate not in seen:
            seen.add(candidate)
            files.append(candidate)
    for raw in re.findall(r"`([^`]+)`", _strip_fenced_blocks(text)):
        candidate = raw.strip()
        if _looks_like_file_path(candidate) and candidate not in seen:
            seen.add(candidate)
            files.append(candidate)
    return files


def _packet_routing_flags(text: str) -> tuple[bool, bool]:
    files = _files_from_packet(text)
    return len(files) >= 1, _packet_has_acceptance(text)


def _files_from_entity(attrs: dict[str, Any]) -> list[str]:
    files = attrs.get("files_expected") or []
    if isinstance(files, str):
        return [files]
    return list(files)


def _files_from_scope(scope_text: str) -> list[str]:
    paths = re.findall(r"`([^`]+)`", scope_text)
    return [p.strip() for p in paths if _looks_like_file_path(p)]


def _acceptance_from_packet(text: str) -> list[str]:
    guidance = _extract_block(text, "task_guidance") or ""
    lines = [line.strip() for line in guidance.splitlines() if line.strip()]
    numbered = [line for line in lines if re.match(r"^\d+\.", line)]
    if numbered:
        return numbered
    if _ACCEPTANCE_HEADING.search(text):
        return ["Packet acceptance criteria documented in headings"]
    if _ACCEPTANCE_PHRASE.search(text):
        return ["Packet acceptance criteria documented in body"]
    return ["Packet acceptance criteria satisfied"]


def _acceptance_from_entity(attrs: dict[str, Any], name: str) -> list[str]:
    raw = attrs.get("acceptance_criteria") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return [f"Complete {name}"]
