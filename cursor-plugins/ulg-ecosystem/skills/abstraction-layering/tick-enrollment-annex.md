# Abstraction layering — tick enrollment annex (L3)

Load on demand when authoring or verifying a layering charter CHECKPOINT. L2 carries
the binding rules; this file holds the byte-identical template and status tables.

## Initial CHECKPOINT template (BINDING)

**When:** hanging a layering work item on the charter runner — mint a `charter-runner`
root (`agent_bus send` with `enroll_charter_runner=true`; tag alone is `422
reserved_enrollment_tag`).

**Order:** (1) mint scoreboard at
`cortex://notes/system/threads/{slug}-scoreboard.md`; (2) stamp todo attrs (skill
§ Stage 0), including `arc_lane=layer` for layer charter enrollments; (3) post the
CHECKPOINT below on a **new** root slug `layer-{todo-slug}` (new enrollments; legacy
`densify-{todo-slug}` roots remain valid if already live).

**Attendance — attended default; autonomous with `arc_lane=layer`.** Leave `attendance`
unset for the attended default. Layer enrollments **MAY** set `attendance=autonomous`
**iff** the todo also stamps `arc_lane=layer` (recommended for clarity) — that pair
selects `materializer_layer.py` (layer-native packets). **Default when `arc_lane` is
unset = `layer`** (G6 / operator 2026-07-30): `attendance=autonomous` without an
explicit `arc_lane` attr still runs and receives **layer** packets — not path-sim.
Stamp explicit `arc_lane=path_sim` only for deepen / non-layer / fat-packet arcs.
The tick logs `charter.tick.arc_lane.unset` when `arc_lane` is missing (informational).
G3/G4 evidence:
`scripts/model_manager/ui/controller/charter_runner/window_exec/materializer_layer.py` ·
dogfood `agent-bus:6489` · closeout
`cortex://notes/system/threads/6467-g4-dogfood-closeout.md`. See § Wired vs pending.

Attendance is now resolved **per root** (todo attr → bus tag `attendance:autonomous`
→ default `attended`); the old `CHARTER_ADMISSION_MODE` env knob is retired, so
"leave it unset" means *stamp nothing* — there is no runner-wide mode to check.

**Admit gate (fail-closed):** missing `##` sections, non-gated Next-pickup, or RESUME
prefix ≠ `— RESUME (any seat, no command):` → tick skips admit. `agent_bus send` does
**not** validate — author correctly here.

Replace `{…}`; keep section headings and RESUME **byte-identical**. Mark gates already
closed as `[x]` with their artifact URI. **G1 closed** only when the todo carries an active
structural `derived_from` → `document:` with `consult_kind=architecture` (skill § G1 skip) —
cite that document's `source_uri` in the `[x]` line; do not mark G1 closed from a bare path.

```
TYPE: CHECKPOINT

## Profile
`tick_charter` (charter-runner enrolled)

## Anchor
- Thread: agent-bus:{ROOT_ID}
- Window: 1 · layering arc
- Todo: {TODO_SLUG}
- Scoreboard: {SCOREBOARD_URI}

## State
**Primary OPEN:** G{ENTRY}–G6 (abstraction layering).
**WIP:** none.
**Recommended overlay (not a G-row):** after-ship `cdp/opus-5` `purpose=review` of landed code — good default. Background after G6; deferral is sequencing. ¬ Next-pickup · ¬ done-claim.

## Steps
1. [ ] G1 — architecture verdict + target shape · [consult:judgment_gap]
2. [ ] G2 — frame (Opus → densifier instructions, ≤120 lines) · [consult:judgment_gap]
3. [ ] G3 — densify dense spec + Gate-2 close · [judgment]
4. [ ] G4 — merged check · [judgment]
5. [ ] G5 — implement (Composer, source_ref) · [implement]
6. [ ] G6 — verify + close (gates · ACs · docstrings) · [inline]

## WIP / In-flight
_None this window._

## Next pickup
1. G{ENTRY} — {GATE_TITLE} · {TODO_SLUG} · executor_lane: judgment · detent=standard

## Frictions
_None this window._

## Sidecars
- Scoreboard: {SCOREBOARD_URI}
- Dense spec / stub: {SPEC_URI}
- Inherited: {ARTIFACT_URI for each gate marked [x]}

## Precedents / Implications
_None this window._

## BLOCKED
None.

## Scoreboard URI
{SCOREBOARD_URI}

— RESUME (any seat, no command): load agent-bus-discipline (§ Standing root threads + § R12 completeness gate; cursor coding arc may also load orchestrator-workflow) → read {SCOREBOARD_URI} → this is the latest CHECKPOINT (wave/in-flight/next above). Do not read the thread linearly. empty Next-pickup ≠ arc complete.
```

**Next-pickup lane rule:** declare `executor_lane: implement` **only** for a G5
proper code edit on an already implement-ready item naming a single `todo:<slug>`;
everything else stays `judgment`. Ambiguity fails closed to judgment.

## Tick lane mechanics (verified against `gate_lane_classifier`)

| Mechanic | Consequence for a layering charter |
|---|---|
| Accepted token set is exactly `[consult:r_admit]` · `[consult:judgment_gap]` · `[implement]` · `[inline]` · `[judgment]` | Use only these; the layer name never replaces the token |
| The **first open annotated** Steps row decides `window_kind` | Order G1→G6 and mark closed gates `[x]`, or the tick picks the wrong gate |
| **Some** rows annotated but the first open one unclassifiable ⇒ refuse `missing_lane_annotation` (tick skips admit) | Annotate consistently; a half-annotated Steps list is the failure shape |
| **No** rows annotated at all ⇒ **no refuse** — falls back to `default_admission_mode` (`generate`) | Hazard: unannotated G1 would run as worker window. G-ordinal fallback is unimplemented `TODO` — annotate **every** G-row |
| `[consult:*]` on the first open row ⇒ `window_kind=consult` and `admission_mode=consult`, **independent of attendance** | G1/G2 get a consult seat on the first tick — intended for CDP gates |
| Consult admission without `CONSULT_PENDING` logs `classifier_consult_without_consult_pending` | When stopping at G1/G2, post `CONSULT_PENDING` + `consult_role: judgment_gap` in the body |
| The consult packet itself is `materializer_consult` | Prose is path-sim-flavored (R-admit / judgment-gap hosting) — acceptable for judgment-gap CDP consult, **not** layer-specific yet |

## Wired vs pending (do not overclaim)

| Surface | Status |
|---|---|
| Skill + `/layer` command + census/catalog registration | **wired** |
| cursor-auto BRIEFING names this slug for `implement`/`investigate`/`verify` | **wired** |
| Attended charter windows (`generate` / `handoff`) name this skill in the packet floor | **wired** (`materializer._LAYER_FLOOR`) |
| Todo attrs + `required_skills` carrying the lane | **wired** |
| G1–G6 Steps rows, `[lane]` tokens, `executor_lane` parsing | **wired** (generic tick contract — see § Tick lane mechanics) |
| G1/G2 consult stops routing to a consult seat | **wired mechanism**, path-sim-flavored packet prose |
| `attendance=autonomous` running *this* arc | **wired when `arc_lane=layer` or unset** — `materializer_layer.py`; explicit `arc_lane=path_sim` for deepen arcs (warn `charter.tick.arc_lane.unset` on missing attr) |
| A layer-specific packet materializer | **wired** — `scripts/model_manager/ui/controller/charter_runner/window_exec/materializer_layer.py` selected via `arc_lane=layer` (not a separate `admission_mode`) |

## Dogfood reference

- `cortex://notes/system/threads/charter-tick-rewrite-fable/fable-answer.md`
- `cortex://notes/system/threads/charter-tick-rewrite-fable/opus-grok-instructions.md` (G2)
- `cortex://notes/system/specs/charter-tick-kernel-rewrite.md` (G3 target)
- `agent-bus:6008` t14 — supersession bind carried onto a live operator-proxy arc
