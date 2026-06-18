"""Render six-block implement packet from ImplementSpec."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent_seat.inject_registry import (
    CODING_SESSION_ADVERTISE_SLUGS,
    coding_scope_inject_entity_ids,
)

from implement_admission.admission_read import (
    compute_packet_sha256,
    replace_frontmatter_value,
)
from implement_admission.skill_fs_line import skill_slug_to_fs_line
from implement_admission.spec import ImplementSpec, SourceKind, implement_spec_hash

_TOOL_SURFACE = (
    'fs(sandbox="workspaces"|"cortex", op="read"|"md_read", …), '
    "cortex, pipeline, observability, manage"
)
_EVENT_CATALOG_CRITERION = (
    "Regenerate event catalog (`scripts/gen-event-catalog sync`) and "
    "`scripts/gen-event-catalog check` passes; stage `docs/event-contracts.md`."
)


def _paths_touch_event_catalog(paths: list[str]) -> bool:
    for path in paths:
        if path.startswith("docs/event-contracts"):
            return True
        if not path.endswith(".py"):
            continue
        if not any(
            path.startswith(f"{root}/") for root in ("services", "libs", "systems")
        ):
            continue
        parts = path.split("/")
        if "events" in parts or parts[-1].startswith("events"):
            return True
    return False


@dataclass(frozen=True, slots=True)
class MaterializedPacket:
    path: str
    packet_sha256: str
    text: str


def materialize(spec: ImplementSpec, *, out_dir: Path) -> MaterializedPacket:
    """Write a six-block packet to out_dir and return path + content hash."""
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_hash = spec.provenance.implement_spec_hash or implement_spec_hash(spec)
    slug = spec.source.canonical_ref.replace(":", "-").replace("/", "-")[:80]
    out_path = out_dir / f"implement-{slug}.md"

    pending_text = _render_packet(spec, spec_hash=spec_hash)
    digest = compute_packet_sha256(pending_text)
    text = replace_frontmatter_value(pending_text, "packet_sha256", digest)
    out_path.write_text(text, encoding="utf-8")
    return MaterializedPacket(
        path=str(out_path),
        packet_sha256=digest,
        text=text,
    )


def packet_is_sufficient(text: str) -> bool:
    """True when a materialized packet meets the §3-Q3 sufficiency floor."""
    mcp = _extract_block(text, "mcp_capabilities") or ""
    guidance = _extract_block(text, "task_guidance") or ""
    corpus = _extract_block(text, "corpus") or ""

    mcp_ok = "agent-skills/" in mcp or "fs(" in mcp or "cortex" in mcp
    numbered_ac = bool(re.search(r"^\s*1\.\s", guidance, flags=re.MULTILINE))
    corpus_ok = "Source:" in corpus and "Intent:" in corpus
    return mcp_ok and numbered_ac and corpus_ok


def _extract_block(text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)
    return match.group(1) if match else None


def _skill_read(slug: str) -> str:
    return skill_slug_to_fs_line(slug)


def _render_scope(spec: ImplementSpec) -> str:
    source_ref = spec.source.source_ref
    lines = [
        f"Implement from `{source_ref}`.",
        f"Bounded: {spec.scope.bounded}.",
    ]
    if spec.scope.files_expected:
        lines.append("Files expected:")
        lines.extend(f"- `{f}`" for f in spec.scope.files_expected)
    return " ".join(lines)


def _render_invariants(spec: ImplementSpec, *, spec_hash: str) -> str:
    routing = spec.routing
    routing_note = "gated — no route"
    derivation_lines: list[str] = []
    if routing is not None:
        routing_note = (
            f"{routing.orchestration_mode.value} × {routing.executor_style.value}"
        )
        derivation_lines = [
            f"- mode_rule: {routing.derivation.mode_rule}",
            f"- style_rule: {routing.derivation.style_rule}",
        ]
    lines = [
        f"- Readiness: {spec.readiness.state.value}",
        f"- Routing (derived): {routing_note}",
        *derivation_lines,
        "- implement_spec_hash determinism required.",
    ]
    if spec.skills:
        lines.append(f"- Required skills: {', '.join(spec.skills)}")
    return "\n".join(lines)


def _is_defaulted_acceptance(spec: ImplementSpec) -> bool:
    criteria = spec.acceptance.criteria
    if len(criteria) != 1:
        return False
    name = spec.intent.summary
    default_a = f"Complete {name}"
    default_b = f"Complete work for {spec.source.canonical_ref}"
    return criteria[0] in (default_a, default_b)


def _render_task_guidance(spec: ImplementSpec) -> str:
    routing = spec.routing
    routing_line = "Routing: gated — no route"
    if routing is not None:
        routing_line = (
            f"Routing: {routing.orchestration_mode.value} × "
            f"{routing.executor_style.value}"
        )
    skill_lines = [_skill_read(s) for s in spec.skills]
    criteria = list(spec.acceptance.criteria)
    if _paths_touch_event_catalog(spec.scope.files_expected) and not any(
        "gen-event-catalog" in c or "event catalog" in c.lower() for c in criteria
    ):
        criteria.append(_EVENT_CATALOG_CRITERION)
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, start=1))
    parts = [routing_line, *skill_lines, "## acceptance criteria", numbered]
    if _is_defaulted_acceptance(spec):
        parts.append(
            "> note: acceptance defaulted from source — executor should treat as under-specified"
        )
    parts.append("\nExecute per ImplementSpec v1 materializer.")
    return "\n".join(parts)


# Architecture skill layers required in every MCP-seat handoff packet so the
# reviewer reads the universal invariants + ULG topology/lifecycle before
# findings (handoff.py validate_packet § handoff_packet_missing_arch_skillrefs).
# Emitted unconditionally; deduped against the spec's own required_skills.
_REQUIRED_ARCH_SKILLS: tuple[str, ...] = tuple(
    entity_id.removeprefix("agent_skill:")
    for entity_id in coding_scope_inject_entity_ids()
)


def _render_mcp_capabilities(spec: ImplementSpec) -> str:
    skills = list(spec.skills)
    inject_slugs = set(_REQUIRED_ARCH_SKILLS)
    skills.extend(s for s in _REQUIRED_ARCH_SKILLS if s not in skills)
    for slug in CODING_SESSION_ADVERTISE_SLUGS:
        if slug not in skills and slug not in inject_slugs:
            skills.append(slug)
    lines = [_skill_read(s) for s in skills]
    lines.append(f"Tool surface: {_TOOL_SURFACE}")
    if any(f.endswith(".py") for f in spec.scope.files_expected):
        files = ", ".join(
            f'"{f}"' for f in spec.scope.files_expected if f.endswith(".py")
        )
        lines.append(f'dispatch(tool="quality_gate", files=[{files}])')
    if _paths_touch_event_catalog(spec.scope.files_expected):
        lines.append(
            "Event catalog: `scripts/gen-event-catalog sync` then "
            "`scripts/gen-event-catalog check`; stage `docs/event-contracts.md`."
        )
    return "\n".join(lines)


_MAX_CORPUS_DECK_BYTES = 65_536
_DECK_EMBED_DELIM = "--- PHASE DECK (verbatim) ---"
_SIX_BLOCK_CLOSERS = (
    "</scope>",
    "</invariants>",
    "</task_guidance>",
    "</mcp_capabilities>",
    "</output_format>",
    "</corpus>",
)


def _sanitize_corpus_embed(text: str) -> tuple[str, int]:
    """Neutralize stray six-block closing tags so deck content cannot prematurely
    close the <corpus> block. Mutations are made VISIBLE (``</x>`` -> ``&lt;/x>``)
    and counted so the caller can annotate them (spec §15 A3)."""
    mutated = 0
    out = text
    for closer in _SIX_BLOCK_CLOSERS:
        count = out.count(closer)
        if count:
            mutated += count
            out = out.replace(closer, "&lt;" + closer[1:])
    return out, mutated


def _authoritative_attrs_line(spec: ImplementSpec) -> str | None:
    if spec.source.source_kind not in (SourceKind.TODO, SourceKind.PLAN):
        return None
    uri = spec.source.source_uri
    if uri:
        return f"narrative spec: {uri}; attributes are authoritative"
    return "attributes are authoritative"


def _render_corpus(spec: ImplementSpec) -> str:
    header = [
        f"Source: {spec.source.canonical_ref}",
        f"Intent: {spec.intent.summary}",
    ]
    if spec.intent.description:
        header.append(f"Description: {spec.intent.description}")
    if spec.scope.files_expected:
        preview = spec.scope.files_expected[:8]
        header.append("Files expected: " + ", ".join(f"`{f}`" for f in preview))
        if len(spec.scope.files_expected) > 8:
            header.append(f"(+{len(spec.scope.files_expected) - 8} more)")
    header.append(f"Acceptance criteria count: {len(spec.acceptance.criteria)}")

    # plan_phase: embed the resolved deck VERBATIM so the executor receives the
    # BEFORE/AFTER task bodies. Bypass the metadata-only [:20] truncation; no outer
    # code fence (decks carry nested ``` fences) — the deck rides inside <corpus>.
    if spec.source.source_kind == SourceKind.PLAN_PHASE and spec.scope.deck_body:
        body, mutated = _sanitize_corpus_embed(spec.scope.deck_body)
        notes: list[str] = []
        if mutated:
            notes.append(
                f"[corpus-sanitized: {mutated} six-block closing token(s) "
                "neutralized as &lt;/...> to protect block structure]"
            )
        if len(body.encode("utf-8")) > _MAX_CORPUS_DECK_BYTES:
            body = body.encode("utf-8")[: _MAX_CORPUS_DECK_BYTES - 2048].decode(
                "utf-8", "ignore"
            )
            notes.append(
                "[deck truncated for corpus size; read the complete phase deck via "
                f"this packet's `source_ref: {spec.source.source_ref}` frontmatter]"
            )
        prefix = ("\n".join(notes) + "\n\n") if notes else ""
        return "\n".join(header) + "\n\n" + _DECK_EMBED_DELIM + "\n\n" + prefix + body

    lines = list(header[:20])
    auth_line = _authoritative_attrs_line(spec)
    if auth_line is not None:
        lines.append(auth_line)
    return "\n".join(lines)


def _render_review_attestation_block(spec: ImplementSpec) -> str:
    att = spec.provenance.review_attestation
    if att is None:
        return ""
    reviewer_family = att.reviewer_family if att.reviewer_family is not None else "none"
    spec_hash = att.spec_hash if att.spec_hash is not None else "unbound"
    ids = att.unresolved_blocker_ids
    ids_repr = "[]" if not ids else str(ids)
    lines = [
        "review_attestation:",
        f"  author_family: {att.author_family}",
        f"  disposition: {att.disposition}",
        f"  required: {str(att.required).lower()}",
        f"  reviewer_family: {reviewer_family}",
        f"  risk_tier: {att.risk_tier}",
        f"  spec_hash: {spec_hash}",
        f"  unresolved_blocker_ids: {ids_repr}",
    ]
    return "\n".join(lines)


def _render_packet(spec: ImplementSpec, *, spec_hash: str) -> str:
    source_ref = spec.source.source_ref
    att_block = _render_review_attestation_block(spec)
    frontmatter_lines = [
        "---",
        f"source_ref: {source_ref}",
        f"implement_spec_hash: {spec_hash}",
    ]
    if att_block:
        frontmatter_lines.append(att_block)
    frontmatter_lines.extend(
        [
            "packet_sha256: PENDING",
            "generated_from: implement_admission_v1",
            "---",
            "",
        ]
    )
    frontmatter = "\n".join(frontmatter_lines)

    body = f"""{frontmatter}<scope>
{_render_scope(spec)}
</scope>

<invariants>
{_render_invariants(spec, spec_hash=spec_hash)}
</invariants>

<task_guidance>
{_render_task_guidance(spec)}
</task_guidance>

<mcp_capabilities>
{_render_mcp_capabilities(spec)}
</mcp_capabilities>

<output_format>
Reply on agent-bus with closeout envelope and verification commands.
</output_format>

<corpus>
{_render_corpus(spec)}
</corpus>
"""
    return body
