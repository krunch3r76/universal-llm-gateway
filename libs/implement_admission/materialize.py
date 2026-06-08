"""Render six-block implement packet from ImplementSpec."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from implement_admission.spec import ImplementSpec, implement_spec_hash


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

    text = _render_packet(spec, spec_hash=spec_hash)
    data = text.encode("utf-8")
    out_path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return MaterializedPacket(
        path=str(out_path),
        packet_sha256=f"sha256:{digest}",
        text=text,
    )


def _render_packet(spec: ImplementSpec, *, spec_hash: str) -> str:
    source_ref = spec.source.source_ref
    criteria = spec.acceptance.criteria
    acceptance_md = "\n".join(f"- {c}" for c in criteria)

    scope_lines = [
        f"Implement from `{source_ref}`.",
        f"Bounded: {spec.scope.bounded}.",
    ]
    if spec.scope.files_expected:
        scope_lines.append("Files expected:")
        scope_lines.extend(f"- `{f}`" for f in spec.scope.files_expected)

    routing = spec.routing
    routing_note = "gated — no route"
    if routing is not None:
        routing_note = (
            f"{routing.orchestration_mode.value} × {routing.executor_style.value}"
        )

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
{" ".join(scope_lines)}
</scope>

<invariants>
- Readiness: {spec.readiness.state.value}
- Routing (derived): {routing_note}
- implement_spec_hash determinism required.
</invariants>

<task_guidance>
## acceptance criteria
{acceptance_md}

Execute per ImplementSpec v1 materializer.
</task_guidance>

<mcp_capabilities>
Investigate via fs/cortex tools as needed for this implement arc.
</mcp_capabilities>

<output_format>
Reply on agent-bus with closeout envelope and verification commands.
</output_format>

<corpus>
Source: {spec.source.canonical_ref}
Intent: {spec.intent.summary}
</corpus>
"""
    return body
