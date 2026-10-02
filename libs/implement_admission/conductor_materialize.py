"""Render six-block conductor packet and birth scoreboard from a todo entity.

Called by Stargate ``resolve_source_ref_to_packet`` when ````.
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
    birth_scoreboard,
    is_g_ladder_rows,
    read_tip,
    render_sparse_scoreboard,
    resolve_row_labels,
    resolve_scoreboard_rows,
    scoreboard_tip_uri,
)
from implement_admission.conductor_summon import resolve_summon_mode
from implement_admission.conductor_width_seat import (
    ACTIVE,
    SKEPTIC,
    SKETCH,
    ConductorWidthSeat,
    g3_g5_score_ratify_clause,
)
from implement_admission.conductor_witness import FoldDeps, fold_scoreboard
from implement_admission.materialize import MaterializedPacket, _extract_block
from implement_admission.source_ref import parse_source_ref, todo_slug_from_ref

_CONDUCTOR_USE_LINE = (
    "Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows."
)


def score_play_seat_lines() -> tuple[str, ...]:
    """Seat lines for packet invariants, filled from ACTIVE at render time.

    Width rows follow the one assignment. Bind and review stay on Opus 5.5,
    so a packet cannot keep offering Fable after ACTIVE moves.
    """
    return (
        "- Enumerate · implement · drive: `cursor/grok-4.7` — omitted knobs "
        "follow the card; same slug as the ticker successor.",
        "- Intelligence: G2 and G3→G5 read "
        f"`{ACTIVE.model}` at reasoning_effort={ACTIVE.reasoning_effort}. "
        f"G1 sketcher reads `{SKETCH.model}` at reasoning_effort={SKETCH.reasoning_effort}. "
        f"G4 skeptic reads `{SKEPTIC.model}` at reasoning_effort={SKEPTIC.reasoning_effort} "
        "(one rung above the high rows; cross-family review on the Opus channel). "
        "Bind/review and code review stay `cdp/opus-5.5` at high.",
        "- The house driver enumerates and does not rank rival designs — write "
        "`OPEN FORK:` and stop.",
        "- BIND is one CDP turn; a second CDP turn on one row means ENUMERATE "
        "was underspecified.",
    )


def hop_invariant_g3_g5_fragment(
    seat: ConductorWidthSeat | None = None,
) -> str:
    """G3→G5 fragment of the hop invariant, filled at call time.

    Production omits ``seat`` and reads ACTIVE. Tests pass RESTORE so the
    Fable wire renders without editing that assignment.
    """
    return (
        "G3→G5 fires in-process CDP score-ratify "
        f"({g3_g5_score_ratify_clause(seat)}) then continues — a live "
        "summoning chat is not a human stop"
    )


def attended_g3_g5_task_sentence(
    seat: ConductorWidthSeat | None = None,
) -> str:
    """Attended G3→G5 task-guidance sentence, filled at call time.

    Uses the same clause as the hop invariant and the GIW preamble so one
    assignment names the seat in every rendered packet.
    """
    return (
        "G3→G5 attended: fire in-process CDP score-ratify "
        f"({g3_g5_score_ratify_clause(seat)}, do-not-fight / likely-optimal), "
        "then continue."
    )


def _default_entry_gate(
    *,
    density_triage: str | None,
    derived_from: str | None,
    rows: tuple[str, ...],
) -> str:
    """Pick the first open row for custom lists; preserve G-ladder defaults."""
    if derived_from:
        return "G2" if is_g_ladder_rows(rows) else rows[0]
    triage = (density_triage or "").strip().lower()
    if triage == "mechanical":
        return "G5" if is_g_ladder_rows(rows) else rows[0]
    return "G1" if is_g_ladder_rows(rows) else rows[0]


class CortexReader(Protocol):
    """Minimal cortex read surface for todo entity_get during materialize."""

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RematerializeContext:
    """Scoped-leg keys round-tripped on conductor hop rematerialize (S4b P1)."""

    hop_seq: int | None = None
    predecessor_dispatch_id: str | None = None
    scoreboard_tip_sha: str | None = None
    scoreboard_entry_gate: str | None = None
    summoning_thread_id: str | None = None
    summoning_thread_id_unresolved: bool = False

    def effective_summoning(
        self, *, top_level_summoning_thread_id: str | None
    ) -> tuple[str | None, bool]:
        """Top-level ``dispatch_thread_id`` wins over ``generation_options``."""
        if self.summoning_thread_id_unresolved:
            return None, True
        top = str(top_level_summoning_thread_id or "").strip()
        if top:
            return top, False
        gen = str(self.summoning_thread_id or "").strip()
        if gen:
            return gen, False
        return None, False


def rematerialize_context_from_dispatch(
    *,
    hop_seq: int | None = None,
    hop_from: str | None = None,
    dispatch_thread_id: str | None = None,
    generation_options: dict[str, Any] | None = None,
) -> RematerializeContext | None:
    """Build scoped-leg context from hop ``team_dispatch`` body carriers (A5 table)."""
    opts = dict(generation_options or {})
    unresolved = bool(opts.get("summoning_thread_id_unresolved"))
    gen_summoning = str(opts.get("summoning_thread_id") or "").strip() or None
    top_summoning = str(dispatch_thread_id or "").strip() or None
    tip_sha = str(opts.get("scoreboard_tip_sha") or "").strip() or None
    entry_gate_meta = str(opts.get("scoreboard_entry_gate") or "").strip() or None
    predecessor = str(hop_from or "").strip() or None
    has_any = (
        hop_seq is not None
        or predecessor is not None
        or tip_sha is not None
        or entry_gate_meta is not None
        or gen_summoning is not None
        or top_summoning is not None
        or unresolved
    )
    if not has_any:
        return None
    return RematerializeContext(
        hop_seq=hop_seq,
        predecessor_dispatch_id=predecessor,
        scoreboard_tip_sha=tip_sha,
        scoreboard_entry_gate=entry_gate_meta,
        summoning_thread_id=gen_summoning,
        summoning_thread_id_unresolved=unresolved,
    )


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
    summon_mode: str
    problem: str | None
    scope: str | None
    acceptance: str | None
    fold_missing_witnesses: dict[str, str] | None = None
    summoning_thread_id: str | None = None
    summoning_thread_id_unresolved: bool = False
    rematerialize: RematerializeContext | None = None
    rows: tuple[str, ...] = G_ROWS
    row_labels: dict[str, str] | None = None


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
    rows = resolve_scoreboard_rows(attrs)
    row_labels = resolve_row_labels(rows, attrs)
    entry_gate = resolve_entry_gate(
        density_triage=attrs.get("density_triage"),
        derived_from=derived_from,
        fold_entry_gate=fold_entry_gate,
        rows=rows,
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
    )


def _render_scope(ctx: ConductorMaterializeContext) -> str:
    lines = [
        f"Conductor session for `{ctx.source_ref}`.",
        "Drive the G-ladder forward-only; mutate upcoming rows only.",
        f"Entry gate: {ctx.entry_gate}.",
        f"Scoreboard tip: `{scoreboard_tip_uri(ctx.slug)}`.",
        "Checkout: Lane B (explicit).",
        f"summon_mode: {ctx.summon_mode}.",
    ]
    if ctx.summoning_thread_id_unresolved:
        lines.append(
            "summoning_thread_id: unresolved — post SCORE_RESURFACE only after "
            "resolving the parent thread."
        )
    elif ctx.summoning_thread_id:
        lines.append(f"summoning_thread_id: {ctx.summoning_thread_id}.")
        lines.append(
            "SCORE_RESURFACE posts to that parent/root thread, not this worker thread."
        )
    if ctx.rematerialize is not None:
        rm = ctx.rematerialize
        if rm.hop_seq is not None:
            lines.append(f"hop_seq: {rm.hop_seq} (hop metadata).")
        if rm.predecessor_dispatch_id:
            lines.append(f"hop_from: {rm.predecessor_dispatch_id} (hop metadata).")
        if rm.scoreboard_tip_sha:
            lines.append(f"scoreboard_tip_sha: {rm.scoreboard_tip_sha} (hop metadata).")
        if rm.scoreboard_entry_gate:
            lines.append(
                f"scoreboard_entry_gate: {rm.scoreboard_entry_gate} "
                "(hop metadata — not live entry_gate)."
            )
    if ctx.fold_missing_witnesses:
        lines.append("CLAIMED rows — attach witnesses, do not re-derive:")
        for row_id in ctx.rows:
            if row_id in ctx.fold_missing_witnesses:
                lines.append(
                    f"- {row_id} CLAIMED: {ctx.fold_missing_witnesses[row_id]}."
                )
    if ctx.stop_after:
        lines.append(f"stop_after pin: {ctx.stop_after}.")
    return " ".join(lines)


def _render_invariants(ctx: ConductorMaterializeContext) -> str:
    lines = [
        _CONDUCTOR_USE_LINE,
        *list(score_play_seat_lines()),
        "- DONE is rendered from witnesses; you hang witnesses, you do not write DONE.",
        (
            "- The continuity card ## Skills lists slugs. After resume, read that section "
            "and Use each slug before the first move."
        ),
        "- Run to completion: admit authorizes landing this mission Lane-B branch on green.",
        "- Nest Composer for mechanical G-rows (`nest_under` this conductor dispatch_id).",
        (
            "- Before you author any `prompt=` or packet body a nested seat will act "
            "on (investigate/confer legs, CDP ask/review gates), Use the "
            "retrieval-before-authoring skill — one `rag` search per scope, yields "
            "and nulls in the leg's report. prompt-expand does not run on this "
            "contract; materialized `source_ref` legs carry no authored prose and "
            "are exempt."
        ),
        "- Forward-only score mutation; journal every tip write.",
        '- lane="B" — pass explicitly on nested mechanical legs.',
        (
            "- Per-G-row hop (binding): at each gated G-row close with no designed stop "
            "owed, append the score journal, write the hop CHECKPOINT on this worker "
            "thread (Anchor · Hop · Mission · Rows · In-flight · Judgment · "
            "Next-pickup · NEXT_ADMIT · RESUME footer), then end this dispatch with "
            "`stop: ROW_HOP` as the last line of your closeout. Do NOT call "
            "team_dispatch with reuse_thread=<this thread> — it is refused while you "
            "are live (422 CURSOR_WORKER_THREAD_OCCUPIED, holder = you). The substrate "
            "admits your successor on this same thread and Lane-B checkout after your "
            "row goes terminal. ROW_HOP is not a pause and not a page; the mission "
            "continues under this admit. Owed stops win: stop_after ⇒ ROW_PINNED; "
            "explicit see-score or OPERATOR_GATE ⇒ ROW_PINNED; "
            f"{hop_invariant_g3_g5_fragment()}; a live nested child "
            "is a cursor-sdk dispatch admitted with nest_under equal to this "
            "conductor dispatch_id (status queued, admitted, running, or "
            "parked_waiting), including its nested descendants — harvest that "
            "child before the hop. A team_dispatch(model=cdp/…) review is not "
            "that child: nest_under is refused unless seat=cursor-sdk, so a "
            "still-running CDP review does not forbid stop: ROW_HOP; the "
            "substrate defers the successor while that review streams on the "
            "mission lane (P1.2, live_external_gate); that is not a stall."
        ),
        (
            "- Density hop (GIW steer): when a same-row steer asks you to write the "
            "ten-field hop CHECKPOINT with Next-pickup = the open G-row and end with "
            "`stop: ROW_HOP`, do that and stop the dispatch. GIW measures context "
            "for you."
        ),
        (
            "- A block this row cannot pass by repeating the same refusal is a gate, "
            "not `stop: ROW_PINNED`. File the friction, clear the pin, and continue "
            "the row. Do not end the hop on that same unpaid gate. Do not revert "
            "lands already on master outside this todo's files_expected."
        ),
    ]
    if ctx.derived_from:
        lines.append(
            f"- G1 CLOSED by derived_from:{ctx.derived_from}. Do not re-derive architecture."
        )
    if ctx.density_triage:
        lines.append(f"- density_triage: {ctx.density_triage}.")
    return "\n".join(lines)


def _render_task_guidance(ctx: ConductorMaterializeContext) -> str:
    if ctx.summon_mode == "attended":
        g3_g5_lines = [
            (
                f"{attended_g3_g5_task_sentence()} "
                "CDP harvest alone does not close G5. Post SCORE_RESURFACE on "
                f"summoning_thread_id={ctx.summoning_thread_id or '<parent/root>'} "
                "(never this worker thread) citing CDP exec id + review sha — "
                "required attended G5 fold witness (a:37198), not implement "
                "completeness. Tip DONE / steer-inject are not closers. Fold "
                "already shows bus:SCORE_RESURFACE ⇒ do not re-post. The report "
                "is not a stop."
            ),
            (
                "Human gate only on explicit see-score or OPERATOR_GATE: "
                "ROW_PINNED. A live summoning chat is not a gate."
            ),
        ]
    else:
        g3_g5_lines = [
            "G3→G5 default: in-process CDP score-ratify "
            f"({g3_g5_score_ratify_clause()}) "
            "(do-not-fight / likely-optimal) via "
            "team_dispatch(op=generate, model=cdp/opus-5.5, contract=freeform, "
            "session=ask, dispatch_thread_id=<this worker thread id>).",
            "Explicit see-score: ROW_PINNED at G3 + ping.",
        ]
    ac = [
        "Spawn receipt quotes dispatch_id + scoreboard URI + Lane B.",
        f"Resume at persisted row (entry gate {ctx.entry_gate}).",
        "Mode B admit-proof on CHECKPOINT: execution_id+poll_hint or honest halt.",
        *g3_g5_lines,
        (
            "Review leg — one dispatch, one wait. "
            "1. Evidence before the dispatch. Before the single G6 team_dispatch, "
            "write an evidence block into the review body: git diff --stat "
            "<base>..HEAD; the pre-land suite summary lines the spec names "
            "(same command on base and on tip); and gen-event-catalog check "
            "output when the diff touches a docstring that feeds a generated "
            "catalog region. A line that was not run is `evidence: absent — <name>` "
            "only when it cannot run in this row — name why. "
            "Run that missing command before the review dispatch. Do not discover "
            "the gap by receiving a WITHHOLD and then starting another row. "
            "2. One dispatch. G6 is team_dispatch(op=generate, model=cdp/opus-5.5, "
            "contract=delivery-review, dispatch_thread_id=<this worker "
            "thread id>, parent_thread omitted or equal to that thread id, "
            "prompt=<body>); the body carries job=delivery-review and "
            "retrieval_report: cortex://… each on its own line, plus the evidence "
            "block. Cite libs/claude_bundles/nested_cdp_prompt_gate.py. The row "
            "does not search the repo for that contract. G5 ratify stays the "
            "session=ask sentence above. "
            "3. One wait, then stop. Wait once with agent_bus tool=wait bound to "
            "that dispatch's poll_hint and execution_id. If the store is still "
            "running after that wait, write poll_hint and execution_id into the "
            "hop CHECKPOINT, add NEXT_ADMIT: harvest <execution_id>, and end with "
            "stop: ROW_HOP. Do not chain agent_bus_read. A successor whose "
            "CHECKPOINT carries a G6 execution_id harvests that id and does not "
            "dispatch G6. A second verdict on the same gate is commentary unless "
            "the scoreboard Stops stamp changes."
        ),
        f"stop_after={ctx.stop_after!r}: run bound leg before ROW_PINNED when set.",
    ]
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(ac, start=1))
    return "\n".join(
        [
            "contract: conductor session",
            numbered,
            "",
            render_sparse_scoreboard(
                source_ref=ctx.source_ref,
                slug=ctx.slug,
                entry_gate=ctx.entry_gate,
                stop_after=ctx.stop_after,
                rows=ctx.rows,
                row_labels=ctx.row_labels,
            ),
        ]
    )


def _render_mcp_capabilities(ctx: ConductorMaterializeContext) -> str:
    return "\n".join(
        [
            "Use the `conductor` skill",
            "Use runbook `score-play` — cortex://notes/runbooks/score-play.md",
            "Use the `work-item-seed-path` skill",
            "Use the `architecture-invariants` skill",
            "Use the `ulg-architecture` skill",
            "Use the `retrieval-before-authoring` skill (before authoring a nested prompt)",
            "Nested CDP width prompts: cite `retrieval_report: cortex://…` (Queries/Yields/Choice-to-evidence); contract path libs/claude_bundles/nested_cdp_prompt_gate.py (the row does not search the repo for it); ¬ delivery chrome on G4/SKEPTIC (a:37183)",
            f'Scoreboard tip: fs(op="read", path="{scoreboard_tip_uri(ctx.slug)}")',
            f'Journal: fs(op="read", path="cortex://notes/system/scoreboards/{ctx.slug}-score-journal.md")',
        ]
    )


def _render_corpus(ctx: ConductorMaterializeContext) -> str:
    header = [
        f"Source: {ctx.source_ref}",
        f"Intent: Conductor unify — {ctx.name}",
        f"Entry gate: {ctx.entry_gate}",
        f"summon_mode: {ctx.summon_mode}",
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
            f"work_key: {ctx.source_ref}",
            "packet_kind: conductor",
            "role_name: conductor",
            "contract: conductor",
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
On every hop that authored a nested CDP prompt: list each prompt URI + its `retrieval_report:` bundle URI (or fail closeout — a:37183).
Designed stop tokens (last lines of final message when owed): stop: ROW_HOP | ROW_PINNED | HOLD_MERGE | OPERATOR_GATE | PARKED_TRANSPORT | DONE
On ROW_HOP closeout include hop_seq: <n> as the last line after stop: ROW_HOP.
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
    return bool(re.search(r"lane:\s*B\b", text, re.IGNORECASE))


def extract_scoreboard_uri(text: str) -> str | None:
    """Pull scoreboard tip URI from a materialized conductor packet."""
    block = _extract_block(text, "task_guidance") or text
    match = re.search(
        r"cortex://notes/system/scoreboards/[^\s`\"']+-scoreboard\.md", block
    )
    return match.group(0) if match else None


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
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"conductor-{slug}.md"

    pending = _render_packet(ctx)
    digest = compute_packet_sha256(pending)
    text = replace_frontmatter_value(pending, "packet_sha256", digest)
    out_path.write_text(text, encoding="utf-8")

    if write_scoreboard and read_tip(slug, files_root=files_root) is None:
        scoreboard_body = render_sparse_scoreboard(
            source_ref=ctx.source_ref,
            slug=ctx.slug,
            entry_gate=ctx.entry_gate,
            stop_after=ctx.stop_after,
            rows=ctx.rows,
            row_labels=ctx.row_labels,
        )
        birth_scoreboard(
            slug,
            scoreboard_body=scoreboard_body,
            seat="materializer",
            dispatch_id=None,
            reason="conductor spawn birth",
            rows=tuple(ctx.rows),
            delta="sparse birth",
            files_root=files_root,
        )

    return MaterializedPacket(path=str(out_path), packet_sha256=digest, text=text)
