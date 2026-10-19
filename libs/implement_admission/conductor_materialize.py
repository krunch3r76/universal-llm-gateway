"""Render six-block conductor packet and birth scoreboard from a todo entity.

Called by Stargate ``resolve_source_ref_to_packet`` when ````.
Writes workspaces packet + cortex scoreboard tip/journal birth record.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from implement_admission.admission_read import (
    compute_packet_sha256,
    replace_frontmatter_value,
)
from implement_admission.conductor_context import (
    ConductorMaterializeContext,
    CortexReader,
    RematerializeContext,
    rematerialize_context_from_dispatch,  # noqa: F401
)
from implement_admission.conductor_packet_render import (
    _default_entry_gate,
    _render_packet,
    attended_g3_g5_task_sentence,  # noqa: F401
    conductor_packet_contains_use_line,  # noqa: F401
    conductor_packet_has_lane_b,  # noqa: F401
    extract_scoreboard_uri,  # noqa: F401
    hop_invariant_g3_g5_fragment,  # noqa: F401
)
from implement_admission.conductor_policy_snapshot import SCOREBOARD_SEED_KEYS
from implement_admission.conductor_score_journal import (
    G_ROWS,
    birth_scoreboard,
    read_tip,
    render_sparse_scoreboard,
    resolve_row_labels,
    resolve_scoreboard_rows,
)
from implement_admission.conductor_score_table import SCOREBOARD_ROW_ID
from implement_admission.conductor_summon import resolve_summon_mode
from implement_admission.conductor_witness import FoldDeps, fold_scoreboard
from implement_admission.materialize import MaterializedPacket
from implement_admission.source_ref import parse_source_ref, todo_slug_from_ref


def conductor_opts_from_generation_options(
    generation_options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Extract ``generation_options.conductor`` when present."""
    if not generation_options:
        return {}
    block = generation_options.get("conductor")
    return dict(block) if isinstance(block, dict) else {}


_ROW_ID_VALID = re.compile(rf"^{SCOREBOARD_ROW_ID}$", re.IGNORECASE)


def _normalize_conductor_rows(raw: Any) -> tuple[str, ...] | None:
    if not isinstance(raw, list) or not raw:
        return None
    cleaned: list[str] = []
    for item in raw:
        rid = str(item).strip()
        if not rid:
            return None
        if not _ROW_ID_VALID.match(rid):
            return None
        cleaned.append(rid)
    return tuple(cleaned)


def validate_conductor_opts_seeds(
    conductor_opts: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    """Return (field, reason) when conductor seed knobs are invalid at admit."""
    if not conductor_opts:
        return None
    rows_raw = conductor_opts.get("rows")
    effective_rows = G_ROWS
    if rows_raw is not None:
        if not isinstance(rows_raw, list):
            return ("conductor.rows", "conductor.rows must be a list")
        normalized = _normalize_conductor_rows(rows_raw)
        if normalized is None:
            return (
                "conductor.rows",
                "conductor.rows must be non-empty valid row ids (G1–G7 or R<n>)",
            )
        if normalized != G_ROWS:
            return (
                "conductor.rows",
                "conductor.rows must be the full G ladder (G1 through G7)",
            )
        effective_rows = normalized
    entry_gate = conductor_opts.get("entry_gate")
    if entry_gate is not None and str(entry_gate).strip():
        gate = str(entry_gate).strip()
        if gate not in effective_rows:
            return (
                "conductor.entry_gate",
                "conductor.entry_gate must be one of conductor.rows",
            )
    stop_after = conductor_opts.get("stop_after")
    if stop_after is not None and str(stop_after).strip():
        stop = str(stop_after).strip()
        if stop not in effective_rows:
            return (
                "conductor.stop_after",
                "conductor.stop_after must be one of conductor.rows",
            )
    return None


def _conductor_seed_merge_allowed(
    *,
    hop_seq: int,
    slug: str,
    conductor_opts: Mapping[str, Any] | None,
    scoreboard_files_root: Path | None,
) -> tuple[bool, bool]:
    """Return (merge_seeds, birth_seed_ignored) for scoreboard seed knobs."""
    has_seed = bool(
        conductor_opts and SCOREBOARD_SEED_KEYS & set(conductor_opts.keys())
    )
    tip_present = read_tip(slug, files_root=scoreboard_files_root) is not None
    if hop_seq == 0 and not tip_present:
        return True, False
    return False, has_seed


def _merge_conductor_seed_rows(
    attrs: dict[str, Any],
    rows: tuple[str, ...],
    conductor_opts: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if attrs.get("rows") or not conductor_opts:
        return rows
    raw = conductor_opts.get("rows")
    if isinstance(raw, list) and raw:
        cleaned = tuple(str(r).strip() for r in raw if str(r).strip())
        if cleaned:
            return cleaned
    return rows


def _merge_conductor_seed_stop_after(
    attrs: dict[str, Any],
    stop_after: str | None,
    conductor_opts: Mapping[str, Any] | None,
) -> str | None:
    if attrs.get("stop_after") or stop_after or not conductor_opts:
        return stop_after
    raw = conductor_opts.get("stop_after")
    return str(raw).strip() if raw else stop_after


def _merge_conductor_seed_entry_gate(
    entry_gate: str,
    fold_entry_gate: str | None,
    conductor_opts: Mapping[str, Any] | None,
) -> str:
    if fold_entry_gate or not conductor_opts or not conductor_opts.get("entry_gate"):
        return entry_gate
    return str(conductor_opts["entry_gate"]).strip()


def resolve_entry_gate(
    *,
    density_triage: str | None,
    derived_from: str | None = None,
    fold_entry_gate: str | None = None,
    rows: tuple[str, ...] = G_ROWS,
) -> str:
    """Pick the G-row entry gate from a witness fold or sparse birth defaults."""
    if fold_entry_gate:
        return fold_entry_gate
    return _default_entry_gate(
        density_triage=density_triage,
        derived_from=derived_from,
        rows=rows,
    )


def load_conductor_context(
    source_ref: str,
    *,
    cortex: CortexReader,
    summon_mode: str | None = None,
    caller_agent: str | None = None,
    summon_text: str | None = None,
    summoning_turn_count: int | None = None,
    fold_entry_gate: str | None = None,
    fold_missing_witnesses: dict[str, str] | None = None,
    summoning_thread_id: str | None = None,
    summoning_thread_id_unresolved: bool = False,
    rematerialize: RematerializeContext | None = None,
    conductor_opts: Mapping[str, Any] | None = None,
    hop_seq: int | None = None,
    scoreboard_files_root: Path | None = None,
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
    effective_hop = (
        hop_seq
        if hop_seq is not None
        else int(rematerialize.hop_seq or 0)
        if rematerialize is not None
        else 0
    )
    merge_seeds, birth_seed_ignored = _conductor_seed_merge_allowed(
        hop_seq=effective_hop,
        slug=slug,
        conductor_opts=conductor_opts,
        scoreboard_files_root=scoreboard_files_root,
    )
    seed_opts = conductor_opts if merge_seeds else None
    derived = attrs.get("derived_from")
    derived_from = str(derived).strip() if derived else None
    stop_raw = attrs.get("stop_after")
    stop_after = str(stop_raw).strip() if stop_raw else None
    rows = resolve_scoreboard_rows(attrs)
    stop_after = _merge_conductor_seed_stop_after(
        attrs, stop_after, seed_opts
    )
    rows = _merge_conductor_seed_rows(attrs, rows, seed_opts)
    row_labels = resolve_row_labels(rows, attrs)
    entry_gate = resolve_entry_gate(
        density_triage=attrs.get("density_triage"),
        derived_from=derived_from,
        fold_entry_gate=fold_entry_gate,
        rows=rows,
    )
    entry_gate = _merge_conductor_seed_entry_gate(
        entry_gate, fold_entry_gate, seed_opts
    )
    explicit_summon = summon_mode
    if explicit_summon is None:
        todo_summon = attrs.get("summon_mode")
        if todo_summon:
            explicit_summon = str(todo_summon).strip()
    resolved_summon_mode = resolve_summon_mode(
        explicit=explicit_summon,
        caller_agent=caller_agent,
        summon_text=summon_text,
        summoning_turn_count=summoning_turn_count,
    )
    return ConductorMaterializeContext(
        source_ref=source_ref,
        slug=slug,
        name=str(entity.get("name") or ref.canonical_ref),
        density_triage=attrs.get("density_triage"),
        derived_from=derived_from,
        stop_after=stop_after,
        entry_gate=entry_gate,
        summon_mode=resolved_summon_mode,
        problem=attrs.get("problem") or attrs.get("Problem"),
        scope=attrs.get("scope") or attrs.get("Scope"),
        acceptance=attrs.get("acceptance") or attrs.get("Acceptance"),
        fold_missing_witnesses=fold_missing_witnesses,
        summoning_thread_id=summoning_thread_id,
        summoning_thread_id_unresolved=summoning_thread_id_unresolved,
        rematerialize=rematerialize,
        rows=rows,
        row_labels=row_labels,
        birth_seed_ignored=birth_seed_ignored,
    )


@dataclass(frozen=True, slots=True)
class RenderedConductorPacket:
    """Pure render product — no disk writes."""

    text: str
    packet_sha256: str
    birth_scoreboard_markdown: str | None
    packet_rel_path: str
    birth_rows: tuple[str, ...] = G_ROWS


def render_conductor_packet(
    ctx: ConductorMaterializeContext,
    *,
    out_dir: Path,
    repo_root: Path | None = None,
    scoreboard_files_root: Path | None = None,
    hop_seq: int = 0,
    write_birth_scoreboard: bool = True,
    tip_body: str | None = None,
) -> RenderedConductorPacket:
    """Render six-block conductor packet bytes without writing disk."""
    effective_tip_body = tip_body
    if hop_seq >= 1 and effective_tip_body is None:
        tip = read_tip(ctx.slug, files_root=scoreboard_files_root)
        if tip is not None:
            effective_tip_body = tip[0]
    pending = _render_packet(ctx, hop_seq=hop_seq, tip_body=effective_tip_body)
    digest = compute_packet_sha256(pending)
    text = replace_frontmatter_value(pending, "packet_sha256", digest)
    rel_name = f"conductor-{ctx.slug}.md"
    rel = rel_name
    if repo_root is not None:
        try:
            rel = (
                (out_dir / rel_name)
                .resolve()
                .relative_to(repo_root.resolve())
                .as_posix()
            )
            if repo_root.name == "universal-llm-gateway":
                rel = f"universal-llm-gateway/{rel}"
        except ValueError:
            rel = (
                f"universal-llm-gateway/tmp/implement-admission/materialized/{rel_name}"
            )
    birth_body: str | None = None
    if write_birth_scoreboard and hop_seq == 0:
        birth_body = render_sparse_scoreboard(
            source_ref=ctx.source_ref,
            slug=ctx.slug,
            entry_gate=ctx.entry_gate,
            stop_after=ctx.stop_after,
            rows=ctx.rows,
            row_labels=ctx.row_labels,
        )
    return RenderedConductorPacket(
        text=text,
        packet_sha256=digest,
        birth_scoreboard_markdown=birth_body,
        packet_rel_path=rel,
        birth_rows=tuple(ctx.rows),
    )


def materialize_conductor(
    source_ref: str,
    *,
    cortex: CortexReader,
    out_dir: Path,
    write_scoreboard: bool = True,
    files_root: Path | None = None,
    summon_mode: str | None = None,
    caller_agent: str | None = None,
    summon_text: str | None = None,
    summoning_turn_count: int | None = None,
    fold_deps: FoldDeps | None = None,
    summoning_thread_id: str | None = None,
    rematerialize: RematerializeContext | None = None,
) -> MaterializedPacket:
    """Write conductor six-block packet; birth scoreboard/journal only when tip absent.

    When ``write_scoreboard`` is true and ``read_tip`` finds an existing tip for the
    todo slug, skip ``birth_scoreboard`` so a re-admit cannot rewind forward progress.
    When a tip exists and ``fold_deps`` is supplied, fold witness projection first.
    """
    slug = todo_slug_from_ref(source_ref)
    effective_summoning = summoning_thread_id
    summoning_unresolved = False
    if rematerialize is not None:
        effective_summoning, summoning_unresolved = rematerialize.effective_summoning(
            top_level_summoning_thread_id=summoning_thread_id
        )
    fold_entry_gate: str | None = None
    fold_missing: dict[str, str] | None = None
    folded_tip_body: str | None = None
    hop_seq = int(rematerialize.hop_seq or 0) if rematerialize else 0
    if read_tip(slug, files_root=files_root) is not None and fold_deps is not None:
        fold_summoning = fold_deps.summoning_thread_id or effective_summoning
        effective_deps = FoldDeps(
            cortex=fold_deps.cortex,
            bus=fold_deps.bus,
            nested_implement=fold_deps.nested_implement,
            git=fold_deps.git,
            source_ref=source_ref,
            summon_mode=fold_deps.summon_mode
            or resolve_summon_mode(
                explicit=summon_mode,
                caller_agent=caller_agent,
                summon_text=summon_text,
                summoning_turn_count=summoning_turn_count,
            ),
            summoning_thread_id=fold_summoning,
            repo=fold_deps.repo,
        )
        fold = fold_scoreboard(slug, deps=effective_deps, files_root=files_root)
        if fold is not None:
            fold_entry_gate = fold.entry_gate
            fold_missing = fold.missing_witnesses or None
            folded_tip_body = fold.folded_body

    ctx = load_conductor_context(
        source_ref,
        cortex=cortex,
        summon_mode=summon_mode,
        caller_agent=caller_agent,
        summon_text=summon_text,
        summoning_turn_count=summoning_turn_count,
        fold_entry_gate=fold_entry_gate,
        fold_missing_witnesses=fold_missing,
        summoning_thread_id=effective_summoning,
        summoning_thread_id_unresolved=summoning_unresolved,
        rematerialize=rematerialize,
        hop_seq=hop_seq,
        scoreboard_files_root=files_root,
    )
    repo = files_root
    if hop_seq >= 1 and folded_tip_body is None:
        tip_pair = read_tip(slug, files_root=files_root)
        if tip_pair is not None:
            folded_tip_body = tip_pair[0]
    rendered = render_conductor_packet(
        ctx,
        out_dir=out_dir,
        repo_root=repo,
        scoreboard_files_root=files_root,
        hop_seq=hop_seq,
        write_birth_scoreboard=write_scoreboard
        and read_tip(slug, files_root=files_root) is None,
        tip_body=folded_tip_body if hop_seq >= 1 else None,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"conductor-{slug}.md"
    out_path.write_text(rendered.text, encoding="utf-8")

    if (
        write_scoreboard
        and read_tip(slug, files_root=files_root) is None
        and rendered.birth_scoreboard_markdown
    ):
        birth_scoreboard(
            slug,
            scoreboard_body=rendered.birth_scoreboard_markdown,
            seat="materializer",
            dispatch_id=None,
            reason="conductor spawn birth",
            rows=tuple(ctx.rows),
            delta="sparse birth",
            files_root=files_root,
        )

    return MaterializedPacket(
        path=str(out_path), packet_sha256=rendered.packet_sha256, text=rendered.text
    )
