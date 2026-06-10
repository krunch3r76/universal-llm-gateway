"""Render six-block implement packet from ImplementSpec."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from implement_admission.admission_read import (
    compute_packet_sha256,
    replace_frontmatter_value,
)
from implement_admission.spec import ImplementSpec, implement_spec_hash

_TOOL_SURFACE = (
    'fs(sandbox="workspaces"|"cortex", op="read"|"md_read", …), '
    "cortex, pipeline, observability, manage"
)


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
    return f'fs(sandbox="cortex", op="md_read", path="agent-skills/{slug}.md")'


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
    numbered = "\n".join(
        f"{i}. {c}" for i, c in enumerate(spec.acceptance.criteria, start=1)
    )
    parts = [routing_line, *skill_lines, "## acceptance criteria", numbered]
    if _is_defaulted_acceptance(spec):
        parts.append(
            "> note: acceptance defaulted from source — executor should treat as under-specified"
        )
    parts.append("\nExecute per ImplementSpec v1 materializer.")
    return "\n".join(parts)


def _render_mcp_capabilities(spec: ImplementSpec) -> str:
    lines = [_skill_read(s) for s in spec.skills]
    lines.append(f"Tool surface: {_TOOL_SURFACE}")
    if any(f.endswith(".py") for f in spec.scope.files_expected):
        files = ", ".join(
            f'"{f}"' for f in spec.scope.files_expected if f.endswith(".py")
        )
        lines.append(f'dispatch(tool="quality_gate", files=[{files}])')
    return "\n".join(lines)


def _render_corpus(spec: ImplementSpec) -> str:
    lines = [
        f"Source: {spec.source.canonical_ref}",
        f"Intent: {spec.intent.summary}",
    ]
    if spec.intent.description:
        lines.append(f"Description: {spec.intent.description}")
    if spec.scope.files_expected:
        preview = spec.scope.files_expected[:8]
        lines.append("Files expected: " + ", ".join(f"`{f}`" for f in preview))
        if len(spec.scope.files_expected) > 8:
            lines.append(f"(+{len(spec.scope.files_expected) - 8} more)")
    lines.append(f"Acceptance criteria count: {len(spec.acceptance.criteria)}")
    return "\n".join(lines[:20])


def _render_packet(spec: ImplementSpec, *, spec_hash: str) -> str:
    source_ref = spec.source.source_ref
    frontmatter = "\n".join(
        [
            "---",
            f"source_ref: {source_ref}",
            f"implement_spec_hash: {spec_hash}",
            "packet_sha256: PENDING",
            "generated_from: implement_admission_v1",
            "---",
            "",
        ]
    )

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
