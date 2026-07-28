"""Resolve which executor bind admits one autonomous charter window.

Wave 2 of the G-row executor routing review (`cortex://notes/system/threads/
g-row-executor-routing-review-5705.md`). The arc's mechanical G4 implement step
does not need the Grok judgment bind it inherits today, but binding *model
family* to a positional G-ordinal is the weakest link in the design: `G1..G6`
are prose conventions inside ``materializer_autonomous._task_guidance``, not a
validated schema, and a pickup routinely names two of them
(``G4 — implement the R-admitted bind (G3 ADMIT)``).

The asymmetry of the failure modes sets the policy. A wrong Grok window costs
five minutes; a wrong Composer window puts a mechanical executor on a judgment
step and produces a confidently-wrong artifact the arc then builds on. So:

- **Declared token first.** ``executor_lane: implement`` on the Next-pickup row
  is authoritative; the G-ordinal table is only a fallback heuristic.
- **Fail closed to judgment.** Absence, ambiguity, revise rows, a missing
  ``source_ref``, or any non-autonomous admission mode keeps today's Grok bind.
  Status quo as the safe default makes the migration monotone.
- **No ungated implement.** ``contract=implement`` reaches
  ``require_implement_ready`` through packet *front matter* only
  (``generate_wrap.prepare_implement_packet`` overwrites the body's
  ``source_ref`` with the front-matter value, and ``frontmatter_value`` returns
  ``None`` for a packet with no ``---`` region — the gate then no-ops
  entirely). Returning the implement lane without a resolvable ``source_ref``
  would hand a mechanical executor an unreviewed edit lane on the one substrate
  whose governing invariant is ``[R-independence] … Autonomous ≠ self-certify``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .checkpoint_parse import ParsedCheckpoint
from .window_terminal_contract import implement_ready_declared

JUDGMENT_LANE = "judgment"
IMPLEMENT_LANE = "implement"

# G4 proper only. Revise rows (G4a/G4b/G4c) stay on the judgment bind: under
# ``contract=implement`` the worker sets ``deliverables_expected=True``
# unconditionally, so a probe-fail revise window — which the autonomous packet
# designs to end in a CHECKPOINT and no file change — would be labeled degraded
# and poison the harvest's reading of that label (review §6). Judgment about
# *why* a probe failed is also the work Grok is better at.
_IMPLEMENT_ROWS = frozenset({"G4"})
_GATED_ROW_RE = re.compile(r"\b([GR]\d+)([a-z]?)\b")


@dataclass(frozen=True)
class ExecutorBind:
    """Which lane admits this window, and the one-line reason it was chosen."""

    lane: str
    reason: str
    source_ref: str | None = None

    @property
    def is_implement(self) -> bool:
        return self.lane == IMPLEMENT_LANE


def _pickup_rows(parsed: ParsedCheckpoint) -> list[str]:
    return [item for item in parsed.next_pickup if item.strip()]


def gated_row_classes(text: str) -> set[str]:
    """Return the gated row ids in ``text``, revise suffixes preserved.

    ``G4`` and ``G4a`` are different classes: the bare ordinal is the implement
    step, the suffixed one is a revise cycle.
    """
    return {f"{base}{suffix}" for base, suffix in _GATED_ROW_RE.findall(text)}


def _heuristic_lane(rows: list[str]) -> tuple[str, str]:
    """Fallback G-ordinal routing when no lane is declared."""
    classes: set[str] = set()
    for row in rows:
        classes |= gated_row_classes(row)
    if not classes:
        return JUDGMENT_LANE, "no_gated_id"
    implement_ids = classes & _IMPLEMENT_ROWS
    if not implement_ids:
        return JUDGMENT_LANE, "no_implement_row"
    if classes - _IMPLEMENT_ROWS:
        # e.g. "G4 — implement the R-admitted bind (G3 ADMIT)" names two
        # classes; which one the window is *about* is not recoverable here.
        return JUDGMENT_LANE, "ambiguous_gated_ids"
    return IMPLEMENT_LANE, "heuristic_g4"


def resolve_charter_executor(
    *,
    parsed: ParsedCheckpoint,
    admission_mode: str,
    consult_role: str | None = None,
) -> ExecutorBind:
    """Return the executor bind for one window; judgment unless implement is proven.

    Must be called **after** the consult branch has settled ``admission_mode``:
    a CONSULT_PENDING pickup that happens to name G4 has to stay on the consult
    seat or the R-independence invariant breaks (review §5).
    """
    if admission_mode == "operator_proxy":
        return ExecutorBind(JUDGMENT_LANE, "operator_proxy_host")
    if consult_role is not None or admission_mode != "autonomous":
        if (
            parsed.executor_lane == IMPLEMENT_LANE
            and parsed.source_ref
            and implement_ready_declared(parsed)
        ):
            return ExecutorBind(
                IMPLEMENT_LANE,
                "declared_implement_ready",
                source_ref=parsed.source_ref,
            )
        # ``generate`` mode materializes a one-gated-step packet with no G-row
        # arc in its task_guidance — routing it to implement would authorize
        # work the packet never described.
        return ExecutorBind(JUDGMENT_LANE, f"mode_{admission_mode}")
    if parsed.executor_lane_ambiguous:
        return ExecutorBind(JUDGMENT_LANE, "declared_lane_ambiguous")
    if parsed.executor_lane == JUDGMENT_LANE:
        return ExecutorBind(JUDGMENT_LANE, "declared_judgment")

    if parsed.executor_lane == IMPLEMENT_LANE:
        lane, reason = IMPLEMENT_LANE, "declared_implement"
    else:
        lane, reason = _heuristic_lane(_pickup_rows(parsed))
    if lane != IMPLEMENT_LANE:
        return ExecutorBind(JUDGMENT_LANE, reason)
    if not parsed.source_ref:
        # Front matter cannot be stamped, so require_implement_ready would
        # no-op on the fired packet. Never trade the gate for the faster model.
        return ExecutorBind(JUDGMENT_LANE, "implement_source_ref_unresolved")
    return ExecutorBind(IMPLEMENT_LANE, reason, source_ref=parsed.source_ref)
