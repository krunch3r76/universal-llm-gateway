"""Render six-block conductor packet and birth scoreboard from a todo entity.

Called by Stargate ``resolve_source_ref_to_packet`` when ``packet_kind=conductor``.
Writes workspaces packet + cortex scoreboard tip/journal birth record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from implement_admission.admission_read import (
    compute_packet_sha256,
    replace_frontmatter_value,
)
from implement_admission.conductor_score_journal import (
    G_ROWS,
    birth_journal_record,
    render_sparse_scoreboard,
    scoreboard_tip_uri,
    write_birth_scoreboard,
)
from implement_admission.materialize import MaterializedPacket, _extract_block
from implement_admission.source_ref import parse_source_ref, todo_slug_from_ref

_CONDUCTOR_USE_LINE = (
    "Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows; "
    "cost tier from this skill."
)
_G_ROWS = G_ROWS


class CortexReader(Protocol):
    """Minimal cortex read surface for todo entity_get during materialize."""

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ConductorMaterializeContext:
    """Resolved todo attrs used to build a conductor spawn packet."""

    source_ref: str
    slug: str
    name: str
    density_triage: str | None
    derived_from: str | None
    stop_after: str | None
    entry_gate: str
    problem: str | None
    scope: str | None
    acceptance: str | None


def resolve_entry_gate(
    *,
    density_triage: str | None,
    derived_from: str | None,
) -> str:
    """Pick the G-row entry gate from todo attrs (sheet bind i + Q1)."""
    if derived_from:
        return "G2"
    triage = (density_triage or "").strip().lower()
    if triage == "mechanical":
        return "G5"
    return "G1"


def load_conductor_context(
    source_ref: str,
    *,
    cortex: CortexReader,
) -> ConductorMaterializeContext:
    """Read todo attrs and derive conductor spawn context."""
    ref = parse_source_ref(source_ref)
    if ref.source_kind != "todo":
        msg = f"conductor materialize requires todo: source_ref, got {source_ref!r}"
        raise ValueError(msg)
    entity = cortex.entity_get(ref.canonical_ref, intent="full")
    if not entity or entity.get("id") is None:
        msg = f"todo entity not found for {source_ref!r}"
        raise ValueError(msg)
    attrs = entity.get("attributes") or {}
    slug = todo_slug_from_ref(source_ref)
    derived = attrs.get("derived_from")
    derived_from = str(derived).strip() if derived else None
    stop_raw = attrs.get("stop_after")
    stop_after = str(stop_raw).strip() if stop_raw else None
    entry_gate = resolve_entry_gate(
        density_triage=attrs.get("density_triage"),
        derived_from=derived_from,
    )
    return ConductorMaterializeContext(
        source_ref=source_ref,
        slug=slug,
        name=str(entity.get("name") or ref.canonical_ref),
        density_triage=attrs.get("density_triage"),
        derived_from=derived_from,
        stop_after=stop_after,
        entry_gate=entry_gate,
        problem=attrs.get("problem") or attrs.get("Problem"),
        scope=attrs.get("scope") or attrs.get("Scope"),
        acceptance=attrs.get("acceptance") or attrs.get("Acceptance"),
    )


def _render_scope(ctx: ConductorMaterializeContext) -> str:
    lines = [
        f"Conductor session for `{ctx.source_ref}`.",
        "Drive the G-ladder forward-only; mutate upcoming rows only.",
        f"Entry gate: {ctx.entry_gate}.",
        f"Scoreboard tip: `{scoreboard_tip_uri(ctx.slug)}`.",
        "Checkout: Lane B (explicit).",
    ]
    if ctx.stop_after:
        lines.append(f"stop_after pin: {ctx.stop_after}.")
    return " ".join(lines)


def _render_invariants(ctx: ConductorMaterializeContext) -> str:
    lines = [
        _CONDUCTOR_USE_LINE,
        "- Run to completion: admit authorizes landing this mission Lane-B branch on green.",
        "- Nest Composer for mechanical G-rows (`nest_under` this conductor dispatch_id).",
        "- Forward-only score mutation; journal every tip write.",
        "- lane=\"B\" — pass explicitly on nested mechanical legs.",
    ]
    if ctx.derived_from:
        lines.append(f"- G1 skip: derived_from → `{ctx.derived_from}`.")
    if ctx.density_triage:
        lines.append(f"- density_triage: {ctx.density_triage} (≠ implement_ready until G5).")
    return "\n".join(lines)


def _render_task_guidance(ctx: ConductorMaterializeContext) -> str:
    ac = [
        "Spawn receipt quotes dispatch_id + scoreboard URI + Lane B.",
        f"Resume at persisted row (entry gate {ctx.entry_gate}).",
        "Mode B admit-proof on CHECKPOINT: execution_id+poll_hint or honest halt.",
        "G3→G5 default: in-process CDP score-ratify (do-not-fight / likely-optimal).",
        "Explicit see-score: ROW_PINNED at G3 + ping.",
        f"stop_after={ctx.stop_after!r}: run bound leg before ROW_PINNED when set.",
    ]
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(ac, start=1))
    return "\n".join(
        [
            "contract: light-bounded conductor session",
            numbered,
            "",
            render_sparse_scoreboard(
                source_ref=ctx.source_ref,
                slug=ctx.slug,
                entry_gate=ctx.entry_gate,
                stop_after=ctx.stop_after,
            ),
        ]
    )


def _render_mcp_capabilities(ctx: ConductorMaterializeContext) -> str:
    return "\n".join(
        [
            "Use the `conductor` skill",
            "Use the `work-item-seed-path` skill",
            "Use the `architecture-invariants` skill",
            "Use the `ulg-architecture` skill",
            f"Scoreboard tip: fs(op=\"read\", path=\"{scoreboard_tip_uri(ctx.slug)}\")",
            f"Journal: fs(op=\"read\", path=\"cortex://notes/system/scoreboards/{ctx.slug}-score-journal.md\")",
        ]
    )


def _render_corpus(ctx: ConductorMaterializeContext) -> str:
    header = [
        f"Source: {ctx.source_ref}",
        f"Intent: Conductor unify — {ctx.name}",
        f"Entry gate: {ctx.entry_gate}",
    ]
    if ctx.problem:
        header.append(f"Problem: {ctx.problem}")
    if ctx.scope:
        header.append(f"Scope: {ctx.scope}")
    if ctx.acceptance:
        header.append(f"Acceptance: {ctx.acceptance}")
    if ctx.derived_from:
        header.append(f"derived_from: {ctx.derived_from}")
    return "\n".join(header)


def _render_packet(ctx: ConductorMaterializeContext) -> str:
    frontmatter = "\n".join(
        [
            "---",
            "packet_kind: conductor",
            f"work_key: {ctx.source_ref}",
            "role_name: conductor",
            "contract: light-bounded",
            "lane: B",
            "packet_sha256: PENDING",
            "generated_from: conductor_materialize_v1",
            "---",
            "",
        ]
    )
    return f"""{frontmatter}<scope>
{_render_scope(ctx)}
</scope>

<invariants>
{_render_invariants(ctx)}
</invariants>

<task_guidance>
{_render_task_guidance(ctx)}
</task_guidance>

<mcp_capabilities>
{_render_mcp_capabilities(ctx)}
</mcp_capabilities>

<output_format>
CLOSEOUT JSON with status, G-row progress, scoreboard tip sha, journal record id.
Include recon_method when breadth recon was owed.
Declare land_disposition on Lane-B branch retirement.
</output_format>

<corpus>
{_render_corpus(ctx)}
</corpus>
"""


def conductor_packet_contains_use_line(text: str) -> bool:
    """True when packet carries the mandatory conductor Use-line."""
    return bool(re.search(r"Use the `?conductor`? skill", text, re.IGNORECASE))


def conductor_packet_has_lane_b(text: str) -> bool:
    """True when frontmatter or invariants name Lane B."""
    return bool(re.search(r'lane:\s*B\b', text, re.IGNORECASE))


def extract_scoreboard_uri(text: str) -> str | None:
    """Pull scoreboard tip URI from a materialized conductor packet."""
    block = _extract_block(text, "task_guidance") or text
    match = re.search(r"cortex://notes/system/scoreboards/[^\s`\"']+-scoreboard\.md", block)
    return match.group(0) if match else None


def materialize_conductor(
    source_ref: str,
    *,
    cortex: CortexReader,
    out_dir: Path,
    write_scoreboard: bool = True,
    files_root: Path | None = None,
) -> MaterializedPacket:
    """Write conductor six-block packet + birth scoreboard/journal; return hash."""
    ctx = load_conductor_context(source_ref, cortex=cortex)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = ctx.slug
    out_path = out_dir / f"conductor-{slug}.md"

    pending = _render_packet(ctx)
    digest = compute_packet_sha256(pending)
    text = replace_frontmatter_value(pending, "packet_sha256", digest)
    out_path.write_text(text, encoding="utf-8")

    if write_scoreboard:
        scoreboard_body = render_sparse_scoreboard(
            source_ref=ctx.source_ref,
            slug=ctx.slug,
            entry_gate=ctx.entry_gate,
            stop_after=ctx.stop_after,
        )
        tip_sha = write_birth_scoreboard(
            slug,
            scoreboard_body=scoreboard_body,
            files_root=files_root,
        )
        birth_journal_record(
            slug,
            prior_tip_sha=None,
            tip_sha=tip_sha,
            seat="materializer",
            dispatch_id=None,
            reason="conductor spawn birth",
            rows=tuple(_G_ROWS),
            delta="sparse birth",
            files_root=files_root,
        )

    return MaterializedPacket(path=str(out_path), packet_sha256=digest, text=text)
