"""Render R1 sketch packet from a todo entity (``contract=sketch`` materializer)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from implement_admission.admission_read import (
    compute_packet_sha256,
    replace_frontmatter_value,
)
from implement_admission.materialize import MaterializedPacket, _extract_block
from implement_admission.source_ref import parse_source_ref, todo_slug_from_ref

_SKETCH_USE_LINE = "Use the `hypothesize-simulate` skill — rival-fill before binding."


class CortexReader(Protocol):
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SketchMaterializeContext:
    source_ref: str
    slug: str
    name: str
    description: str | None
    problem: str | None
    scope: str | None


def load_sketch_context(source_ref: str, *, cortex: CortexReader) -> SketchMaterializeContext:
    ref = parse_source_ref(source_ref)
    if ref.source_kind != "todo":
        msg = f"sketch materializer requires todo: source_ref, got {source_ref!r}"
        raise ValueError(msg)
    entity = cortex.entity_get(ref.canonical_ref, intent="full")
    name = str(entity.get("name") or ref.canonical_ref)
    description = entity.get("description")
    attrs = entity.get("attributes") or {}
    return SketchMaterializeContext(
        source_ref=source_ref,
        slug=todo_slug_from_ref(source_ref),
        name=name,
        description=str(description) if description else None,
        problem=attrs.get("problem") if isinstance(attrs, dict) else None,
        scope=attrs.get("scope") if isinstance(attrs, dict) else None,
    )


def _render_scope_pin(ctx: SketchMaterializeContext) -> str:
    lines = [f"Shape bind for `{ctx.source_ref}` — {ctx.name}."]
    if ctx.problem:
        lines.append(f"Problem: {ctx.problem}")
    if ctx.scope:
        lines.append(f"Scope: {ctx.scope}")
    elif ctx.description:
        lines.append(ctx.description[:2000])
    return "\n".join(lines)


def _render_negative_space() -> str:
    return (
        "Out of scope: implement/land on this leg · repository source edits · "
        "ranking rival designs (enumerate only). "
        "Product is the R1 consult sidecar, not a lane commit."
    )


def _render_output_envelope(ctx: SketchMaterializeContext) -> str:
    sidecar = f"cortex://notes/system/consults/{ctx.slug}-sketch.md"
    return (
        f"Deliverable: consult sidecar at `{sidecar}` with scope_pin, "
        "negative_space, output_envelope, transfer_predicate. "
        "Assert `shape_bind` on the todo when the sidecar lands."
    )


def _render_transfer_predicate() -> str:
    return (
        "This bind holds when the sidecar at the named cortex path carries all "
        "four R1 parts and the todo receives a shape_bind assertion."
    )


def _render_packet(ctx: SketchMaterializeContext) -> str:
    frontmatter = "\n".join(
        [
            "---",
            f"work_key: {ctx.source_ref}",
            "packet_kind: sketch",
            "role_name: sketch",
            "contract: sketch",
            "lane: B",
            "packet_sha256: PENDING",
            "generated_from: sketch_materialize_v1",
            "---",
            "",
        ]
    )
    return f"""{frontmatter}<scope_pin>
{_render_scope_pin(ctx)}
</scope_pin>

<negative_space>
{_render_negative_space()}
</negative_space>

<output_envelope>
{_render_output_envelope(ctx)}
</output_envelope>

<transfer_predicate>
{_render_transfer_predicate()}
</transfer_predicate>

<scope>
Sketch materialize — `{ctx.source_ref}`.
</scope>

<invariants>
- Durable product is cortex:// only.
- No dispatcher prose in the four R1 blocks above.
- {_SKETCH_USE_LINE}
</invariants>

<task_guidance>
contract: sketch
Materialize-only leg: author the consult sidecar; do not implement repository source.
</task_guidance>

<mcp_capabilities>
fs(op="write", path="cortex://notes/system/consults/{ctx.slug}-sketch.md")
</mcp_capabilities>

<output_format>
Closeout with status, sidecar URI, written_sha256 from fs write response.
</output_format>

<corpus>
Source: {ctx.source_ref}
Intent: Sketch shape bind — {ctx.name}
</corpus>
"""


def sketch_packet_has_r1_parts(text: str) -> bool:
    """True when materialized sketch packet carries the four R1 blocks."""
    for tag in ("scope_pin", "negative_space", "output_envelope", "transfer_predicate"):
        block = _extract_block(text, tag)
        if not block or not block.strip():
            return False
    return True


def materialize_sketch(
    source_ref: str,
    *,
    cortex: CortexReader,
    out_dir: Path,
) -> MaterializedPacket:
    """Write sketch packet to *out_dir* and return path + content hash."""
    ctx = load_sketch_context(source_ref, cortex=cortex)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"sketch-{ctx.slug}.md"
    pending = _render_packet(ctx)
    digest = compute_packet_sha256(pending)
    text = replace_frontmatter_value(pending, "packet_sha256", digest)
    out_path.write_text(text, encoding="utf-8")
    return MaterializedPacket(path=str(out_path), packet_sha256=digest, text=text)
