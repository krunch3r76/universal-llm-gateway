"""Conductor packet prose renderers."""

from __future__ import annotations

import re
from typing import Any

from implement_admission.conductor_context import ConductorMaterializeContext
from implement_admission.conductor_critic_render import g3_g5_score_ratify_clause
from implement_admission.conductor_no_restart import (
    G7_RESTART_OWED_RULE,
    NESTED_PROMPT_RULE,
    NO_RESTART_LINE,
)
from implement_admission.conductor_score_journal import (
    is_g_ladder_rows,
    scoreboard_tip_uri,
)
from implement_admission.impresario_policy import ResolvedRoles, resolve_roles
from implement_admission.materialize import _extract_block

_CONDUCTOR_USE_LINE = (
    "Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows."
)


def score_play_seat_lines(roles: ResolvedRoles | None = None) -> tuple[str, ...]:
    """Seat lines for packet invariants, filled from Pattern A at render time."""
    resolved = roles or resolve_roles()
    critic = resolved.critic
    return (
        f"- Enumerate · implement · drive: `{resolved.driver.model}` — omitted knobs "
        "follow the card; same slug as the ticker successor.",
        "- Intelligence: G2 and G3→G5 read "
        f"`{critic.model}` ({critic.transport}). "
        f"G1 sketcher reads `{resolved.sketch.model}`. "
        f"G4 skeptic reads `{resolved.skeptic.model}` "
        "(cross-family review on the configured critic channel). "
        f"G6 delivery-review reads `{resolved.review.model}`.",
        "- The house driver enumerates and does not rank rival designs — write "
        "`OPEN FORK:` and stop.",
        "- BIND is one CDP turn; a second CDP turn on one row means ENUMERATE "
        "was underspecified.",
    )


def hop_invariant_g3_g5_fragment(roles: ResolvedRoles | None = None) -> str:
    """G3→G5 fragment of the hop invariant, filled at call time."""
    return (
        "G3→G5 fires in-process score-ratify "
        f"({g3_g5_score_ratify_clause(roles)}) then continues — a live "
        "summoning chat is not a human stop"
    )


def attended_g3_g5_task_sentence(roles: ResolvedRoles | None = None) -> str:
    """Attended G3→G5 task-guidance sentence, filled at call time."""
    return (
        "G3→G5 attended: fire in-process score-ratify "
        f"({g3_g5_score_ratify_clause(roles)}, do-not-fight / likely-optimal), "
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
        (
            "- G7 land: after the G6 verdict is PASS, call "
            "land_lane_branch_onto_hub_master once with holder_op_id set to this "
            "dispatch id. Never call ff_only_onto_hub_master or "
            "clean_merge_onto_hub_master directly, and never call any land function "
            "to test feasibility (use can_ff_onto_hub_master or "
            "git merge-base --is-ancestor)."
        ),
        "- Nest Composer for mechanical G-rows (`nest_under` this conductor dispatch_id).",
        f"- {NESTED_PROMPT_RULE}",
        f"- {NO_RESTART_LINE}",
        f"- {G7_RESTART_OWED_RULE}",
        (
            "- Before authoring the FIRST nested CDP width prompt (G1/G2/G4/G6) when no "
            "mission retrieval report exists yet, Use the retrieval-before-authoring "
            "skill once (`target: todo:<slug>` in the report sidecar) and reuse that "
            "`retrieval_report: cortex://…` URI across later G4/G6 gates without "
            "re-running `rag` searches. Do not run `rag` for G3/G5 cursor-sdk legs or "
            "when a retrieval report URI is already recorded on the scoreboard/thread."
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


def _render_task_guidance(
    ctx: ConductorMaterializeContext,
    *,
    seats: Any | None = None,
    hop_seq: int = 0,
    tip_body: str | None = None,
) -> str:
    from implement_admission.conductor_task_guidance import (
        render_task_guidance,
        seats_from_roles,
    )

    resolved = seats or seats_from_roles()
    seq = hop_seq
    if ctx.rematerialize is not None and ctx.rematerialize.hop_seq is not None:
        seq = int(ctx.rematerialize.hop_seq)
    return render_task_guidance(ctx, seats=resolved, hop_seq=seq, tip_body=tip_body)


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


def _render_packet(
    ctx: ConductorMaterializeContext,
    *,
    hop_seq: int = 0,
    tip_body: str | None = None,
) -> str:
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
{_render_task_guidance(ctx, hop_seq=hop_seq, tip_body=tip_body)}
</task_guidance>

<mcp_capabilities>
{_render_mcp_capabilities(ctx)}
</mcp_capabilities>

<output_format>
CLOSEOUT JSON with status, G-row progress, scoreboard tip sha, journal record id.
Include recon_method when breadth recon was owed.
Declare land_disposition on Lane-B branch retirement.
On every hop that authored a nested CDP prompt: list each prompt URI + its `retrieval_report:` bundle URI (or fail closeout — a:37183).
{G7_RESTART_OWED_RULE} {NO_RESTART_LINE}
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
