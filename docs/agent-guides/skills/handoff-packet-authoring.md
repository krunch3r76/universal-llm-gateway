# Handoff Packet Authoring

Durable skeleton + checklist for the **stage → densify → wrap → dispatch-by-`source_ref`**
lifecycle — Gate 2 **consult briefs**, **dense specs**, and server-**materialized
implement packets** share the six-block shape. Default bound-implement transport:
`team_dispatch(op=generate, role=cursor-sdk, contract=implement, source_ref=todo:{slug})`
(server materializes from todo attributes; auto Composer, no IDE pickup). Promoted out
of ephemeral `tmp/reviews/_handoff-packet-template.md` so it cannot go missing under task
pressure (incident threads 1296/1297). Authority for the block contract: project
`.cursor/rules/architecture-handoff-protocol.mdc` § "The Six Required Blocks".

**Lightweight complement (different artifact, different audience):** this skill governs the
heavyweight machine-dispatch packet (6 XML blocks, `team_dispatch`). The lightweight,
human-pasteable **fresh-session kickoff/pickup prompt** an operator pastes into a new chat
to start an orchestrator session is governed by `agent-skills/handoff-prompt-authoring.md`
(7-part imperative template). Use that for cold-session kickoffs, this for machine dispatch.

## Spec vs packet — two different artifacts (read first)

A **spec** is the durable *design* (the cargo). A **packet** is an ephemeral
*transport envelope* around it (the container). The same spec is re-wrapped into a
fresh packet for each handoff leg — that loop is intended, not redundant.

| | Spec | Packet |
|---|---|---|
| Nature | Durable design — *what to build* | Ephemeral transport — *one handoff's payload* |
| Path | `tasks/specs/{slug}.md` (RAG `todo_specs`) | `tmp/reviews/{slug}-*.md` (disposable) |
| Lifetime | Persists across sessions | Scoped to a single dispatch |
| Author | Reasoning tier hardens it (web-Opus at densify) | Dispatching seat wraps it per leg |
| Audience | Any agent who later picks up the work | The one model on the other end of *this* dispatch |
| Holds | Problem, scope, touch points, steps, ACs, `<reasoning_trace>`, bound forks | Six XML blocks (scope/invariants/task_guidance/corpus/mcp_capabilities/output_format) |
| Why it exists | Single source of truth for the design | Injects what the receiver is blind to (workspace rules, invariants, investigation targets, output contract) |

**Naming discipline — never bare "packet".** Always qualify:
- **consult brief** — Gate 2 **consult packet** (six-block); front-matter `contract: consult`; `<output_format>` asks the receiver to *produce a dense spec*.
- **dense spec** — durable design at `tasks/specs/{slug}.md`; fingerprinted by `content_hash`, never parsed as the instruction source.
- **materialized implement packet** — Gate 3 six-block transport; `contract: implement`; ACs with the literal word `acceptance` in `<task_guidance>`; asks the receiver to *execute*. Default path: server materializes from `source_ref=todo:{slug}` attributes (`files_expected`, `acceptance_criteria`, `required_skills`).

The two carry **opposite output contracts** (design prose vs code edits) — that is why
the same work needs two packets, and why one cannot be reused as the other.

**The lifecycle loop (intended):**

```
consult packet   →   spec   →   implement packet
  Gate 2 in         Gate 2 out      Gate 3 in
```

`architecture-handoff-protocol.mdc` Gate 3: *"Dense spec ≠ dispatchable packet"*
(friction #16805). A spec is **never** dispatched raw to an executor — it is always
re-wrapped into an implement packet (spec body → `<corpus>`, ACs → `<task_guidance>`).
Web saying *"a spec is not a packet — I owe a hand-authored six-block packet"* is this
rule firing correctly, **not** a verdict that the upstream consult packet was malformed.

## Dispatch lifecycle (when to author which packet)

**Invariant:** Reasoning tier (`web-consult` / `cursor-consult` / Opus) authors
dispatch-ready specs (`tasks/specs/{slug}.md` + todo seed); mechanical tier
(`cursor-sdk` generate / Composer — default; `cursor-implement` handoff fallback)
executes them — **never the reverse**. Because Composer is mechanical, the implement
packet MUST be dense and complete (every file/function/test shape pinned, forks resolved);
an under-specified cursor-sdk packet is a routing error, not a thin spec the executor rescues.

Read `attributes.dispatch_lane` on the leaf `todo:` before writing anything.

| `dispatch_lane` | Who authors | Packet type | Typical seat |
|---|---|---|---|
| `web-implement-packet` | web-claude | six-block **consult brief** that authors a materialized implement packet | `team_dispatch(op=handoff, seat=claude-web)` (shorthand `web-consult`) |
| `web-spec` | web-claude | six-block **consult brief** (findings) | `team_dispatch(web-consult)` |
| `cursor-sdk-implement` *(default for bound implement)* | any dispatching seat | distilled todo attributes (server-materialized implement packet) | `team_dispatch(op=generate, role=cursor-sdk, source_ref=todo:{slug}, contract=implement)` — auto Composer, no IDE pickup |
| `cursor-mechanical` | cursor IDE | skeleton or full packet on disk; **no web** when spec is sufficient | `cursor-sdk` generate (default) · IDE / `cursor-implement` when already in Cursor |
| `cursor-implement` | cursor (handoff) | materialized implement packet with acceptance criteria | `team_dispatch(op=handoff, role=cursor-implement, source_ref=todo:{slug})` — **fallback**: operator opens IDE |
| `operator-gate` | operator | assert template / export — not a handoff packet | — |

**Canonical pipeline:** reasoning upstream (web consult or plan author) → dense artifact
(dense spec + distilled attributes, or phase doc) → mechanical downstream via
`source_ref=todo:{slug}` dispatch (`composer-2.5` / cursor-sdk default).

**Counter-pattern:** mechanical work with a dense todo spec (e.g. corpus export) —
`dispatch_lane: cursor-mechanical`, `density: mechanical`; skip web entirely.

**Codified bug tickets bind to this same pipeline (investigate→execute):** a filed bug/friction
defaults to **investigate + decide** with ordered consult preference: `web-consult`
(web-claude) first, GPT-5.5 generate when the corpus is self-contained, and
`cursor-consult` only when Cursor-seat affordances are required or the operator asks for
Cursor. That reasoning-upstream hop produces the dense spec; **investigate close** MUST
distill `files_expected` / `acceptance_criteria` (+ `required_skills`) onto the bug-fix
`todo:` and record an implement-ready assertion citing the spec + `spec_sha256` (see § Gate 2
step 6 + `consult-routing.md` § Densify lane) → **execute** default =
`team_dispatch(op=generate, role=cursor-sdk, contract=implement, source_ref=todo:{slug})`
(server materialization); web-native inline `fs` fix on web seat; `cursor-implement` /
`web-implement` + `packet_path` = named fallback (§ Gate 3 wrap-exception table).
Do not author a `cursor-implement` packet as the first hop on a bug whose root cause or
design is still open; that collapses the upstream → dense-artifact → downstream pipeline
into a single mechanical step with no spec. **Pass zoom-out duty** binds every bug pickup:
zoom out beyond the filed symptom (touch-point inventory, bug-class grep, labeled
`## Secondary findings` in closeout). Full model: `agent-skills/consult-routing.md`
§ Codified bug reports → Pass zoom-out duty.

Upstream gates (falsifier, operator assert) must close before `web-implement-packet`
dispatch — set `workflow_state: blocked` + `block_reason` on the blocked leaf.

Full attribute table: `universal-llm-gateway/.cursor/rules/todo_ws.mdc` §Dispatch metadata.

## Staging a todo for densification (operator trigger → fixed sequence)

Operator says *"draft a preliminary packet for `todo:{slug}` and submit to web for
densification"* (or *"stage `todo:{slug}` for densification"* / *"stage `todo:{slug}` for
densification and submit to web-claude"*). This is **Gate 2 entry** —
produce a **consult brief**, never a materialized implement packet. "Preliminary packet" = the Gate 2
consult brief (the container that *requests* the dense spec); it is **not** a Gate 3 materialized
implement packet built ahead of the dense spec. Run exactly, every time:

**Authority boundary:** Gate 2 staging is retrieval/scaffolding only. The stager may
perform mechanical synthesis — summarize known constraints, group candidate files, quote
existing assertions, list hypotheses, and name forks — but MUST preserve judgment for the
densifier. ¬ resolve design forks; ¬ select implementation shape; ¬ mark the task
implementation-ready; ¬ author a Gate 3 materialized implement packet. If the next useful step requires
design judgment, write a minimal consult brief with the unresolved forks/questions and
dispatch.

**Triage precondition (declared-state, ¬ inferred).** Whether a sparse todo is densified
by a reasoning tier or staged mechanically is an **escalation** decision — and the tier
that would need to escalate (Composer at staging) is the one empirically least reliable at
making it (`model-tier-awareness.mdc`, thread 807). So it is **declared** by an authorized
reasoner/operator via `attributes.density_triage`, never inferred from prose by the stager:
- `judgment_required` ⟹ this sequence (reasoning-tier densify); ¬ mechanical stage to Composer.
- `mechanical` ⟹ skip densify only with a dense source + `required_skills` + context edge + **no open forks**.
- `unknown` / unset ⟹ implement dispatch **blocked**; consult/densify is the only admissible path — set the triage before proceeding.
A heuristic detector MUST NOT be the arbiter here (sparse-but-mechanical false-positives,
confident-but-unresolved false-negatives, boilerplate gaming); the server enforces the
*declared* state, judgment stays with the human/reasoner (thread 1783 critique).

1. **Verify the lane.** `cortex(entity_get, todo:{slug}, intent=full)` — confirm
   `dispatch_lane ∈ {web-spec, web-implement-packet}` (densify-bound), read `required_skills`,
   prior assertions, source signals. Wrong lane (`cursor-mechanical`, or a dense spec already
   exists) ⟹ densify is the wrong move — say so, do not dispatch.
2. **Seed the stub spec** at `tasks/specs/{slug}.md` (Gate 2b draft): STEP 0 adequacy
   verdict + Problem/Scope skeleton + `<reasoning_trace>` provenance table + **unresolved**
   `§8` forks (Composer surfaces forks blank; does NOT resolve them). Then
   `entity_update(source_uri="tasks/specs/{slug}.md", workflow_state="in_progress")`. The
   stub IS written — folding it only into `<corpus>` and skipping the file is the divergence
   this section closes.
3. **Author the consult brief** at `tmp/reviews/{slug}-harden-web-consult-packet.md`:
   front-matter `contract: consult` + the web boot-gate frontmatter (single canonical
   home: § Web-receiver priming checklist → Frontmatter). Six blocks, Gate-2 shaped
   (skeleton: `handoff-dispatchers.mdc` § Gate 2 packet skeleton). `<corpus>` references the
   stub + todo attributes; `<output_format>` demands a **dense spec**, not v1 patches;
   closeout signal `ready-for-Composer-implement`. **Anchoring guard** — `<corpus>` MUST
   label the stub as a *retrieval index, non-authoritative*: "re-derive from primary
   artifacts; ¬ elaborate candidate structure unless independently confirmed." A cheap
   scaffold silently anchors the densifier into elaborating a flawed design otherwise.
4. **Dispatch.** `team_dispatch(op=handoff, role=web-consult, packet_path=tmp/reviews/{slug}-harden-web-consult-packet.md, subject=…)`.
   ¬ pass a `contract=` param — `consult` is derived from front-matter; the `team_dispatch`
   `contract` enum is `{light-bounded, pure-mechanical, implement}` only, so `contract="consult"`
   is a **422 validation error**.
5. **Hand back.** Report thread id + `push_reminder`. The dense spec lands at
   `tasks/specs/{slug}.md` when web closes `ready-for-Composer-implement`. Gate 3
   (direct `source_ref` implement dispatch) is a **separate, later** step — do NOT
   pre-author an implement packet now.
   See § Gate 3 — direct implement dispatch for the default procedure.
6. **Distill attributes (Gate-2 close, mandatory).** Before declaring implement-ready,
   project the dense spec onto the todo as structured attributes the materializer consumes:
   `files_expected` (non-empty `list[str]`), `acceptance_criteria` (non-empty `list[str]`),
   and `required_skills` when applicable. Use `entity_update` — the materializer for
   `source_ref=todo:{slug}` reads attributes only; prose in `tasks/specs/{slug}.md` is
   fingerprinted by `content_hash`, never content-read. Dispatch rejects (422
   `implement_attrs_unpopulated`) when these attrs are empty or defaulted. Waive the
   advisory session-close detector only via `attributes.attributes_distillation_waived`.

**Entity hygiene is the dispatching seat's duty — not something the reviewer should have to
offer.** A staged-but-stale todo (`unsubstantiated`, no `source_uri`, no tracking assertion)
forces the densify reviewer to either fix workflow-state it doesn't own or hold off and ask —
the exact hesitation seen on thread 1770. Close it at the source:
- **At stage** (step 2 already sets `source_uri` + `workflow_state=in_progress`): also seed a
  tracking assertion on `todo:{slug}` — derivation `agent_observation`, citing the stub spec
  path + dispatch thread (e.g. *"Staged for densification: stub spec at
  `tasks/specs/{slug}.md`, consult packet dispatched to web on thread N."*). This clears the
  *"no spec recorded"* flag and gives the entity a cited artifact.
- **Leave `confidence_band` as-is** at stage — the design is not ratified yet; a stub spec is
  not substantiation. Band promotion comes later, not from the act of staging.
- **At Gate 2 close** (`ready-for-Composer-implement`): record an implement-ready assertion
  citing the now-dense spec; promote `confidence_band` per the ratified design;
  **distill `files_expected` + `acceptance_criteria`** onto the todo (see step 6 above);
  `workflow_state` stays `in_progress` until Gate 3 completes. **Predicate shape:** lead the implement-ready claim with implement-ready intent so it normalizes to `status({todo_id}, implement_ready, current)` — do NOT phrase it "reopened (in_progress)"/"in_progress" (normalizes to an `in_progress` predicate or a `has_attribute` no-match, which the materializer readiness gate ignores); cite the dense spec + `spec_sha256:<hex>` in `evidence_uris`; set `predicate_form` explicitly if the normalizer still mis-targets.

The reviewer never has to ask permission to fix todo hygiene — by this contract it was never
theirs to fix.

## Gate 3 — direct implement dispatch (default: server materialization)

After Gate 2 closes (`ready-for-Composer-implement`) with attributes distilled onto
`todo:{slug}`, **default Gate 3** is one-hop dispatch-by-`source_ref` — the server
materializes the six-block materialized implement packet from todo attributes and
executes via `cursor-sdk`:

```python
team_dispatch(op="generate", role="cursor-sdk", contract="implement",
              source_ref="todo:{slug}", dispatch_thread_id="{arc-id}")
```

Materialization consumes `files_expected`, `acceptance_criteria`, and `required_skills`
from the todo row; dense-spec prose at `tasks/specs/{slug}.md` is fingerprinted by
`content_hash`, never content-read as the instruction source.

### Compliance predicate (all five required)

If any check fails, route per the rejecting branch table below — **do not wrap**.

1. `source_ref=todo:{slug}` resolves to the intended todo.
2. An active implement-ready assertion (`ready-for-Composer-implement`) cites the dense spec **and** its `spec_sha256`.
3. Distilled attrs present/valid: non-empty `files_expected`, non-empty `acceptance_criteria` (+ `required_skills` when applicable); pass `validate_distilled_attributes`.
4. Zero unresolved forks / material decision branches (`validate_dense_spec` passes).
5. The dense-spec artifact is still the one fingerprinted by the cited `spec_sha256` (no spec/attrs drift); else route back and re-distill.

### Rejecting behavior (branch table)

| Condition | Verdict | Action |
|---|---|---|
| Missing/empty distilled attrs | reject `implement_attrs_unpopulated` | distill/backfill, retry |
| No implement-ready assertion | reject (not ready) | return to Gate 2 |
| Open fork / decision remains | reject (not ready) | resolve fork first |
| Spec/attrs (sha) drift | reject `implement_spec_drifted_since_ready` | re-validate, refresh assertion with new `spec_sha256` |
| Need inspect / no-Composer artifact | — | W4 `contract=wrap` |
| Need richer corpus / manual seat | — | W1 `packet_path` / manual handoff |
| Materializer broken + urgent | — | break-glass `packet_path` WITH incident note |

### Anti-pattern triggers (when wrap is correct vs a violation)

**Governing principle: wrap is non-remedial.** Wrap exists for alternate transport or
a richer corpus, never to make an incompliant todo look implementable. A `source_ref`
rejection (gate fired) means fix the todo or route back, not wrap.

**Closed set of legitimate wrap triggers** (the ONLY cases):

| Bucket | Surface | When correct |
|---|---|---|
| Inspection artifact (W4) | `contract=wrap`, `source_ref=todo:{slug}` | Need the materialized packet WITHOUT spawning Composer — pre-dispatch audit, frozen packet for a thread, manual review, provenance/hash-trace. |
| Alternate transport / manual seat (W1) | `packet_path`, or `op=handoff` `cursor-implement`/`web-implement` | Target is a non-sdk/manual seat whose transport is packet-file based. |
| Non-projectable corpus (W1) | `packet_path` | Executor needs corpus content not faithfully representable by `files_expected`+`acceptance_criteria`+`required_skills` (verbatim refs, cross-file narrative). Bar is high: if mechanical-ready the projection suffices; if not, suspect residual design judgment → route back. |
| Break-glass incident (W1) | `packet_path` WITH incident note | `source_ref` materializer temporarily broken + urgent. Label break-glass, not a protocol exception. |

**NOT wrap triggers** (route back / fix precondition):

- Distilled attrs unpopulated → 422 `implement_attrs_unpopulated`; distill/backfill then retry.
- Open fork survives Gate-2 → not implement-ready; return to Gate 2 densification.
- No active implement-ready assertion → gate blocks; record it.
- Multi-todo batch → aggregate into one compliant item or route as `task:`/`plan:`.
- Executor-tier override → carried by `executor_override` request/front-matter field; needs no hand-authored packet.

**Legacy / escape-hatch — inline hand-authored wrap:** when the source is not yet
representable as `todo:`/`plan:`/`plan_phase:` attributes, the materializer output is
known-insufficient for a one-off, or for materialized-vs-hand-authored debug, the
dispatching seat MAY inline-wrap a dense spec into a hand-authored materialized
implement packet (see procedure below), then dispatch with `packet_path` (legacy).

Separate two acts the word "wrap" hides:
**artifact-generation** (write the materialized implement packet) vs **implementation**
(execute via dispatch).

**Wrap — four senses (disambiguation, friction #17374).** "wrap" is overloaded; only W1/W2 are the Gate-3 lifecycle "wrap," and **none** of the four is a Gate-2 densify:
- **W1 — lifecycle Gate-3 wrap (legacy/escape-hatch):** inline mechanical act of wrapping a dense spec into a hand-authored materialized implement packet (current-seat, **no dispatch**).
- **W2 — the act-split inside W1:** *artifact-generation* (write the materialized implement packet — mechanical, inline) vs *implementation* (execute the packet — the dispatch). See the paragraph above.
- **W3 — `generate_wrap.prepare_implement_packet`:** the **server** function that gate-then-materializes a materialized implement packet (used by `contract=implement` + `source_ref` materialization and `contract=wrap`).
- **W4 — `contract=wrap` (landed):** first-class generate-lane transport on `role=cursor-sdk` — server hard-gates implement-ready + ratification, materializes the six-block materialized implement packet via `prepare_implement_packet`, returns HTTP 200 with `packet_path` + provenance **without** spawning Composer. Explicitly invoked (¬ auto-closeout); materialize-only sibling of default `contract=implement` + `source_ref`. Call: `team_dispatch(op=generate, role=cursor-sdk, contract=wrap, source_ref=todo:{slug})` — `source_ref` required, `packet_path` forbidden, `dispatch_thread_id` exempt.

Collision to avoid: a Gate-2 densify whose **subject** is W3/W4 must not be mistaken for a W1 Gate-3 wrap (which forbids dispatch). Cross-family steering on a self-authored W3/W4 design is the friction #17374 misroute.

**Precondition gate (both required).** ¬ wrap or dispatch until:
- an **active implement-ready assertion** cites the dense spec (¬ mere `source_uri` existence; ¬ a `seed_contract_ack`), AND
- the dense spec has **zero OPEN forks** (`§8` empty or explicitly closed).

The "is it dense?" check is now **mechanical**, not eyeballed: the dense spec MUST pass `validate_dense_spec` (`libs/implement_admission/dense_spec_schema.py` — required sections present, non-empty `<reasoning_trace>` attestation, zero live `OPEN:` markers on code-stripped text), the same gate admission re-runs at the cited evidence URI (`todo:dense-spec-schema`).

**Legacy / escape-hatch — inline hand-authored wrap procedure** (current seat — ANY reasoning-authorized tier; ¬ a dispatch):
1. Read `todo:{slug}` + `source_uri`; confirm the implement-ready assertion is active.
2. Verify the dense spec carries: problem/scope, touched files/functions, steps, acceptance criteria, tests/verification, resolved-forks (or explicit "none").
3. Author the materialized implement packet inline at `tmp/reviews/{slug}-implement-packet.md`: spec body → `<corpus>`, ACs → `<task_guidance>` (literal `acceptance`), front-matter `contract: implement`; self-check the six anchored `^<tag>$` blocks.
4. **Halt rule** — if any open fork or design gap surfaces during wrap, STOP and route back to Gate 2 densification. ¬ resolve it inside the wrap; wrapping is transport, not design.
5. **Then** dispatch *implementation* — compliant-default first (when attrs are distilled and gates pass):

```python
# Compliant-default — server materializes from source_ref
team_dispatch(op="generate", role="cursor-sdk", contract="implement",
              source_ref="todo:{slug}", dispatch_thread_id="{arc-id}")
```

Named exception only — hand-authored `packet_path`:

```python
# Legacy / escape-hatch — hand-authored packet_path only
team_dispatch(op="generate", role="cursor-sdk", contract="implement",
              packet_path="tmp/reviews/{slug}-implement-packet.md",
              dispatch_thread_id="{arc-id}")
```

**Antipattern — do NOT dispatch the wrap itself.** Routing the wrap *step* to `cursor-sdk`
(case study `todo:densification-workflow-stage-wrap-tier-policy`, threads 1781/1785) cost a
wrapper packet + a context thread (after `generate` rejected an empty `dispatch_thread_id`)
+ retry + a separate result-thread poll — disproportionate ceremony for inline
packetization the current seat does in one write. The *implementation* dispatch after the
packet existed ran clean (thread 1785: intended files only, compileall/ruff/pytest 45-passed/
import-check green). **`contract=wrap`** is the landed first-class server-materialization
transport for materialize-only (no Composer spawn):
`team_dispatch(op=generate, role=cursor-sdk, contract=wrap, source_ref=todo:{slug})`.
**Server materialization via `source_ref=todo:{slug}` is the documented Gate-3 default** for
bound implement dispatch (`contract=implement`).

## CONFORM — loose-intent → conforming-todo (the stage before Gate 3 / wrap)

**Status: provisional · instrumented recipe** — ratified-with-conditions via consensus-steelman panel (thread 2831; `decision:conform-lane` / assertion 20242). Design SOT: `cortex:notes/system/specs/limb-conformance-from-intent.md`. Routing form: `consult-routing.md` § CONFORM lane.

CONFORM is the transform **one stage before** Gate 3 / wrap. Where wrap (W1–W4) turns a *conforming todo* into a *materialized implement packet*, CONFORM turns *loose intent* into the **conforming todo** that wrap then consumes — its output is exactly wrap's admissible input (an implement-ready `todo:` with zero OPEN forks). The two compose:

```
loose intent → [CONFORM] → conforming todo → [Gate 3 / wrap] → materialized implement packet → [implement]
```

It is the **order-reversed mirror** of the scaffold→densify transform (`Preliminary scaffold (Composer) → densification (reasoner)`, below): there the reasoner *densifies* a worker's scaffold (worker first, judgment second — anchoring risk carried by the reasoner); in CONFORM the reasoner closes every judgment fork **first** in an intent envelope, then the worker derives only structure (judgment first, worker second). Reversing the order dissolves the anchoring hazard, leaving **conformance fidelity** as the sole residual risk.

**Split of labour.** The reasoner authors a fork-free **intent envelope** (semantics): `objective` · `touch_points` · `acceptance_criteria_known` · `judgment_settled` (explicit fork-closure attestation) · `required_skills_hint?`. The `cursor-sdk` worker derives the **G1–G6 admission gates** (structure) into a conforming `todo:`. Recipe-now form is a `light-bounded` generate against a frozen envelope schema (¬ a bespoke contract yet): `team_dispatch(op=generate, role=cursor-sdk, contract=light-bounded, packet_path=<frozen-envelope instance>)`.

**Antipattern distinction (vs the wrap-step-dispatch antipattern above).** The threads 1781/1785 antipattern is dispatching the *one-write wrap step* (inline W1 packetization) — disproportionate ceremony for a single write. CONFORM is **not** that: it dispatches the *heavyweight G1–G6 admission derivation*, genuinely multi-step conformance work that clears the inline-vs-dispatch threshold (`consult-routing.md`). ∀ admission (C1): judgment-settled AND above-threshold. Dispatching the bare wrap step still fails; dispatching G1–G6 derivation passes. ∀ new-protocol codification: **inadmissible** — protocol content is judgment-bearing, authored inline (this section was, dogfooding C1).

**Two-layer verify-back.** Layer-1 = the wrap **precondition gate** run materialize-only — the automated half: does the conforming-todo carry an active implement-ready assertion with zero OPEN forks (the same Gate-3 precondition above)? Layer-2 = a bounded semantic diff of the 3 judgment-bearing fields only (`objective`, `acceptance_criteria_known`, `judgment_settled` attestation) — the reasoner confirms the worker did not silently re-open a closed fork.

**Conditions (binding).**
- **C2 — telemetry every run:** envelope hash, target `todo` + `spec_sha256`, Layer-1 result, Layer-2 verdict, lead active-time, worker closeout, fidelity corrections.
- **C3 — promotion gate:** a first-class `contract=conform` with a `prepare_conforming_todo` server materializer (the W3 `prepare_implement_packet` analogue, mirroring the W3→W4 path) is **BLOCKED** until the decisive falsifier (assertion 20242) clears over N ≥ 5 real (non-dogfood) runs.

## CONVERSE — clarification-dialogue → fork-free intent envelope (the stage before CONFORM)

**Status: provisional · instrumented harness** — ratified-with-conditions via consensus-steelman panel (thread 2846; `decision:converse-lane` / assertion 20260). Design SOT: `cortex:notes/system/specs/limb-clarification-dialogue.md`. Adjudication: `cortex:notes/system/threads/2846-converse-ratification-adjudication.md`. Routing form: `consult-routing.md` § CONVERSE lane. What is canonized is the **harness, not the economic claim** — lead-run only; autonomous `contract=converse` stays blocked until the falsifier clears (C3).

CONVERSE is the transform **one stage before CONFORM** (B feeds A). Where CONFORM assumes the lead has already closed every fork in the intent envelope, CONVERSE is what the lead runs when the intent still carries **latent forks** they have not surfaced: a reasoning-tier worker conducts a **bounded clarification dialogue** with the lead and emits exactly the same fork-free **intent envelope** as output. The stages compose:

```
loose intent (latent forks) → [CONVERSE] → fork-free intent envelope → [CONFORM] → conforming todo → [Gate 3 / wrap] → implement packet
```

Where CONFORM **transcribes** settled judgment into structure, CONVERSE **manufactures** the settlement — which is why its canonicalization is narrowed to the harness only, lead-run only, with policy authorship excluded (C1) and autonomy blocked (C3).

**This is NOT a packet dispatch.** A clarification dialogue is multi-turn, so CONVERSE does **not** author a packet and fire `team_dispatch(op=generate)` (one-shot, no back-and-forth). The worker runs on the **`agent_bus`** (`reply` + server-side `wait`); the only durable artifact is the **emitted intent envelope** that becomes CONFORM's input. Worker tier floor = **reasoning-capable**.

**Budget + termination (structurally enforced).** HARD **3-question-round** budget, enforced by the **harness**, never by worker self-count (assertion 20188). Termination paths: **T1 converge** → emit the fork-free envelope; **T2 budget-exhausted** → halt and emit with `residual_open_forks` (escalate, do not guess); **T3 lead-abort** → no envelope, lead reclaims authoring.

**Attribution discipline (C1).** Forks are surfaced on **lead-decided content only**, never worker policy/protocol authorship. The emitted envelope **distinguishes lead-decided fields from worker-inferred restatements**; any policy choice not explicitly lead-selected stays in `residual_open_forks`; guard against policy **smuggled into the question frame**. ∀ new-protocol codification: inadmissible — same as CONFORM.

**Verify-back is inherited.** CONVERSE emits CONFORM's envelope, so the two-layer verify-back (Layer-1 wrap-gate + Layer-2 bounded semantic diff) applies unchanged — no new surface.

**Conditions (binding).**
- **C2 — telemetry every episode (total cost):** dialogue thread id, rounds used (of 3), `residual_open_forks`, envelope hash, worker tier/model, CONFORM-admissible-unmodified?, **total** lead active-time (setup + reading + answering + adjudicating + post-CONFORM rework + downstream repair — omitting any component is laundering), lead fork-value score.
- **C2-control — randomized control arm (NEW vs CONFORM):** route a ~30–50% control fraction of eligible real tasks to **unaided** authoring, with a pre-routing intake rubric recorded **before** routing. (The spec's original "vs unaided counterfactual" falsifier was rejected by both panel families as unobservable; the control arm makes the baseline real.)
- **C3 — promotion gate:** a first-class `contract=converse` (+ harness turn-cap enforcement) is **BLOCKED** until the revised falsifier (assertion 20260) clears over **N ≥ 8** real episodes with the control arm.

## General execution without packet (contract-based)

**Schema-free is NOT direction-free.** A `cursor-sdk` dispatch for a fully
**determinate** task with **pre-authored** values may omit the six-block packet —
`team_dispatch(op=generate, role=cursor-sdk, dispatch_thread_id=…, contract=light-bounded|pure-mechanical)` —
with explicit instructions pre-staged on the dispatch thread. Composer 2.5 is still a
mechanical executor, so thread-staged directions must be explicit, detailed,
restrictive, and bounded. This is a distinct lane from the materialized implement packet,
**not** a lighter packet. `messages[]` is not on the wire. On `op=generate`, a
`subject` argument is accepted but **ignored** (the result-thread subject is auto-derived);
the response carries a `subject_ignored_on_generate` warning. Use `op=to_thread` to set a
thread subject (friction 19803).

Canonical lane definition — the three-point spectrum (Dense Implement / Light Bounded
Execution / Pure Mechanical Write Loop) — lives in the SOT: `agent-skills/consult-routing.md`
§ General execution lane (contract-based — no packet). Do not duplicate the body here.

**Instruction quality (mandatory):** Load `.cursor/skills/cursor-sdk-instruction-standard/SKILL.md`
before authoring any cursor-sdk dispatch turn. Encodes the four required disciplines
(D1 determinate steps, D2 constraint repetition, D3 mandatory self-check clause,
D4 preflight hard-stop) and the pre-dispatch checklist. Grounded in friction 19196:
a `light-bounded` dispatch missing D2/D3 wrote to the wrong path and under-reported
bound assertions, requiring lead-seat verification to trust the self-reported "done".

## Friction-ticket packets (extra preflight)

When the handoff targets a filed friction (not a todo spec arc):

| Check | Why (16849 / thread 1576) |
|---|---|
| Friction ID resolves to `service:*` assertion | Assertion 16737 was a `task:` completion row, not friction 16737 |
| Bound task not already `done` | Dispatched investigate on a closed arc |
| Corpus names exact `entity_id` | Web queried `service:stargate`; friction lived on `service:universal-stargate` |
| `<mcp_capabilities>` uses assertion lookup, not guessed service | `assertions(entity_id=service:…)` or `frictions` with the **same** service slug |
| Operator confirmed dispatch intent | Typo request → void protocol, not `team_dispatch` |

See `friction-review.md` § Friction ID preflight / Void recall.

## Preflight (mandatory — before writing a packet)

Complete for a **consult** AND a bound **implement** (`role=cursor-implement` or `role=web-implement` handoff):

```
fs(cortex,     agent-skills/consult-routing.md)                         # transport + authority map
fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc)         # md_read § The Six Required Blocks
fs(workspaces, .cursor/rules/handoff-dispatchers.mdc)                   # § target seat
```

The protocol files live at **project** `.cursor/rules/` (one level above the
repo; **no** `universal-llm-gateway/` prefix). Skipping the trio when the boot
card `_CONSULT_ROUTING_GATE` is present is a protocol violation.

### Skill discovery (in-session)

**Web** — MCP `skill_suggest(…)` at inflection points before authoring packet `fs`
lines (`skill-suggest-utilization.md` § Web). **Cursor IDE** — match
`<available_skills>` / Read `.cursor/skills/` stubs; ¬ `skill_suggest`.

When the skill set is already known (todo `required_skills`, static consult corpus),
follow § **Skill load resolution (mandatory)** — never derive a load path from slug alone.

## Skill load resolution (mandatory)

∀ skill slug `S` referenced in a handoff packet:

```
1. cortex(entity_get, id=agent_skill:S) → read source_uri (+ digest if available)
2. Translate source_uri to fs load line:
   workspaces://universal-llm-gateway/… → fs(workspaces, op=read, path=universal-llm-gateway/…)
   agent-skills/foo.md (relative)       → fs(cortex, op=read, path=agent-skills/foo.md)
3. Put translated line in <invariants> or numbered <mcp_capabilities> step
¬ derive path from slug alone
¬ use cortex://agent-skills/{slug}.md without entity_get confirmation
```

### Three path surfaces

| Surface | Root | Example |
|---|---|---|
| `packet_path` | `PROJECT_ROOT` | `tmp/reviews/foo.md` |
| `fs(workspaces)` | `/mnt/torus/projects/` | `universal-llm-gateway/docs/…` or `projects/.cursor/rules/…` |
| `fs(cortex)` | cortex sandbox | `agent-skills/consult-routing.md` |

### Resolved examples (entity_get ground truth — verify live before citing)

| Skill source | Example slug | Packet load line |
|---|---|---|
| Repo SOT (`workspaces://…/docs/agent-guides/skills/…`) | `agent-guidance-writing` | `fs(workspaces, op=read, path="universal-llm-gateway/docs/agent-guides/skills/agent-guidance-writing.md")` |
| Cortex SOT (`agent-skills/…`) | `skill-document-writing` | `fs(cortex, op=read, path="agent-skills/skill-document-writing.md")` |
| Cursor stub SOT | `add-mcp-tool` | `fs(workspaces, op=read, path="universal-llm-gateway/.cursor/skills/add-mcp-tool/SKILL.md")` |

Do not assume every `agent_skill:*` entity has a Cortex `agent-skills/<slug>.md` body.
The Cortex-native analog for MCP surface changes is
`fs(cortex, op=read, path="agent-skills/mcp-surface-change.md")` — not slug-derived from
`add-mcp-tool`.

**Anti-pattern:** posting skill-ref supplements on agent-bus turn 2 after dispatch — all
skill load lines belong in the packet file on turn 1.



### Packet-wired vs session-loaded vs suggested (do not conflate)

| State | Meaning | Where it lives |
|---|---|---|
| **Packet-wired** | An fs-line for the skill is authored in the packet `<invariants>` (by the author or by `enrich_web_handoff_packet`). The artifact references it; the receiver session has **not** read the body yet. | packet `<invariants>`; enrich `skills_added` / `skills_already_wired` |
| **Session-loaded** | The receiver has fetched the body (or boot-critical sections) this session and listed the slug in **`loaded[]`** on subsequent `skill_suggest` calls; plus boot auto-inject slugs in `seat_preloaded`. Session auto-registry from `fs` alone is **target**, not landed. | agent `LOADED` ledger + `skill_suggest` response |
| **Suggested** | `skill_suggest` ranked the slug as a not-yet-loaded delta. | `skill_suggest` response |

∀ web-consult densify pickup: a slug can be **packet-wired but not session-loaded** — so `skill_suggest` will (correctly) surface it. That is confirmatory delta, not a missing-wiring signal. Load the packet `<invariants>` skills, then accept the matching `skill_suggest` hits as confirmation. ¬ treat a packet-wired slug appearing in `skill_suggest` as a fresh discovery or as a packet defect.

Enrich reports the split: `skills_already_wired` (densify slug already present in the packet) vs `skills_added` (newly injected). `handoff-packet-authoring` is now a default densify slug, so the receiver checklist named in the pointer is carried in `<invariants>` rather than learned first from `skill_suggest`.

## Web-receiver priming checklist (mandatory before `web-consult` / `web-implement`)

**Invariant:** claude-web has full MCP but ¬ IDE rules, ¬ `.cursor/skills` auto-load,
¬ terminals. The packet MUST inject what web cannot discover. Completing this checklist
is part of packet authoring — ¬ a post-dispatch supplement (incident: thread 2229).
**When:** ∀ `team_dispatch(op=handoff, role=web-consult|web-implement, packet_path=…)`.

This section is the single canonical home; `lead-seat-boot.md` (receiver-side gate),
`handoff-dispatchers.mdc` § web-claude, and `consult-routing/SKILL.md` cross-ref here.

### Frontmatter (boot gate — receiver halts past Gate 2 if missing)

| Field | Required when |
|---|---|
| `active_project_tag` | project-scoped work (web halts + requests if absent) |
| `cortex_boot_confirmed: true` | cursor already booted this session |
| `related_thread_ids` | any upstream agent-bus thread |
| `todo:` / `plan:` | bound arc (entity slug) |

Enforcement: receiver-side gate is canonical — `lead-seat-boot.md` § Cursor Dispatch
Packet Compliance. Author-side, the server `build_pointer_body` reminder (consult)
backstops; it does not replace this checklist.

### Block 2 `<invariants>` — skill refs (minimum set)

For web handoffs, do **not** assume `architecture-invariants` / `ulg-architecture`
are server-injected on generic boot. They are CODING-scope bodies and generic web
boot runs with `code_touching=False`; manual preload is load-bearing for ULG work.

When the consult touches ULG repo code, MCP, events, git-integration, service
lifecycle, routing, pipelines, or architecture/protocol docs, Block 2 MUST carry
explicit `fs(workspaces, op=read/read_multi, ...)` lines for both
`docs/agent-guides/skills/architecture-invariants.md` and
`docs/agent-guides/skills/ulg-architecture.md`, resolved from
`agent_skill:<slug>.source_uri`. For non-ULG or pure Cortex/document consults,
omit them unless the bound work item's `required_skills` names them.

Enrich may auto-add these lines from `todo:`/`task:` `required_skills` or ULG-surface
heuristics, but packet authors remain responsible for the visible Block 2 contract.
(This differs from `claude-cursor` / `cursor-sdk`, where the arch layer is
description-gated, never injected, and the fs-lines ARE load-bearing — see
`handoff-dispatchers.mdc` § web-claude "Rule/skill surface asymmetry".)

Minimum set for web:
- `lead-seat-boot.md` — web boot gate (or rely on `cortex_boot_confirmed`)
- `consult-routing.md` — when the consult may close implement-ready (post-densify lane)
- **≥1 task-class skill** (see matrix) — resolve via `agent_skill:<slug>.source_uri`,
  never a path guessed from the slug (cortex-native vs `.cursor/skills` differ).
- Bound work item `required_skills`: when `todo:{slug}` or `task:{slug}` exists,
  `cortex(entity_get)` → mirror each as an `fs` line.

Task-class refs (non-exhaustive decision aid — `skill_suggest` + `required_skills` are
the real discovery; reuse the slugs already named in `handoff-dispatchers.mdc` § web-claude):

| Task class | Skill ref |
|---|---|
| MCP surface / routing | `mcp-surface-change.md` |
| Non-trivial edit / SLOC gates | `modularize-discipline.md` |
| Phased / todo implement | `implement-todo.md` · `implementation-plan-workflow.md` |
| Pipeline work | `build-pipeline.md` · `refine-pipeline.md` · `debug-with-events.md` |
| cursor-sdk worker message | `cursor-sdk-instruction-standard.md` |
| Observability / queue forensics | `debug-with-events.md` |
| Re-enable / smoke after change | `service-lifecycle.md` |
| Dispatch / poll handles | `dispatch-shape.md` |
| Executor advisory wire surface (op=handoff / generate `executor_recommendation`) | `architecture-invariants.md` `[universal:executor-rec]` — additive/versioned advisory container; model/thinking/effort independent axes, never collapse effort into thinking |

### Block 4 `<corpus>` — repo pointers (minimum set)

Explicit `fs(workspaces, op=read, path=…)` lines for: primary spec
(`tasks/specs/{slug}.md`) when present; operator pickup/sidecar (`tmp/prompts/…`) when
present; every file in the todo's `files_expected` (or the spec touch-point table);
offline tests for the touched package (`test_*.py`). Label any scaffold as
*retrieval index, non-authoritative — re-derive from primary artifacts* (anchoring guard).

### Block 5 `<mcp_capabilities>` — numbered investigation plan (SHOULD be structured; MUST be concrete)

1. **Boot + skills** — `skill_suggest(loaded=[…], conversation_context=…)` at the inflection
   (`skill-suggest-utilization.md` § Loaded ledger; `web-boot-lead.md` § tiered preload)
   + task-class refs resolved by `source_uri`.
2. **Bus + cortex** — `agent_bus(fetch, thread=<id>)` for **each** `related_thread_ids`
   (MUST — the gap thread 2229 hit); `cortex(entity_get|search)` for the todo + decisions.
3. **Primary code paths** — one numbered `fs(read)` per file web must verify live.
4. **Live probes** — `observability(recent-events, signal_prefix=…)` for signals named
   in the task.

**Anti-pattern:** a generic "you have MCP, investigate" block with <5 concrete steps.

### Pre-dispatch self-check (author — evidence before `team_dispatch`)

- [ ] Frontmatter boot-gate fields present
- [ ] ≥1 task-class skill ref in `<invariants>`, resolved via `source_uri`
- [ ] ULG-touching consults: arch-pair fs-lines in Block 2 (or bound
      `required_skills` names them); ¬ assume generic boot injects arch bodies
- [ ] Bound `todo:`/`task:` `required_skills` mirrored (if a work item exists)
- [ ] ≥1 `agent_bus(fetch)` per upstream thread in `<mcp_capabilities>`
- [ ] Every spec touch-point has a numbered `fs(read)`
- [ ] ≥1 observability probe if the task names a queue/event/live gap
- [ ] Scaffold blocks carry no design judgment (Composer-scaffold stubs only)

Failure → complete the checklist first; ¬ dispatch-then-supplement on thread 2.

## The Six Required Blocks

Author the packet in this order, in canonical XML tags (case-sensitive):

| # | Block | Required | Holds |
|---|---|---|---|
| 1 | `<scope>` | yes | what's reviewed/implemented (branch/HEAD, path) + selection mode |
| 2 | `<invariants>` | yes | compact workspace rules; MCP dispatchers use skill-ref lines + ≤15 task lines |
| 3 | `<task_guidance>` | yes | what to evaluate / do — questions, criteria; **acceptance criteria for `implement`** |
| 4 | `<corpus>` | yes | the artifact under review / context |
| 5 | `<mcp_capabilities>` | iff dispatcher has MCP | reviewer tools + evidence format (claude-web, claude-cursor) |
| 6 | `<output_format>` | yes | finding / closeout shape |
| — | `<excluded>` | optional | files/sections not sent, one-word reason |
| — | `<prior_pass>` | optional | iteration preamble (applied/rejected/surfaced) |

**Implement contract**: acceptance criteria live in `<task_guidance>`; closeout
evidence shape in `<output_format>`. The admission lint (`handoff.py`) rejects an
`implement` packet whose `<task_guidance>` contains no `acceptance` keyword.
Declare authority explicitly via front-matter `contract: implement` or the MCP
`contract=` param on `team_dispatch(op=handoff)` — a packet with acceptance
criteria but no contract signal is rejected (`handoff_contract_ambiguous`).

**Executor override (implement):** optional front-matter or request fields —
server resolves `recommended_executor` on the handoff response (advisory on
manual seats; IDE picker binds the executor tier):

```yaml
executor_override: composer | composer-fast | composer-thinking | web-inline | <non-composer-tier>
executor_override_reason_code: pure_cortex_doc_edit | capability_gap | protocol_heavy | design_judgment_remaining
executor_override_reason: "short required text when reason_code demands it"
```

Silence → `recommended_executor=composer`. See `agent-skills/consult-routing.md`
§ Executor tier for R1/R2 policy (reference, do not hand-copy).

### Block 6 `<output_format>` — worker closeout shape by capability tier (every-packet default)

Every dispatch packet's `<output_format>` states the dispatched worker's closeout
**shape**, chosen by the worker's **capability tier** (read from the dispatch
role/seat):

- **MCP-capable worker** (writes its own bus turn) → write a structured cortex
  sidecar at `notes/system/threads/{thread}-{subject}.md`, then post a **brief bus
  pointer** carrying the sidecar URI + content_hash/sha256 + a one-line summary.
  Discipline target ≤ ~2 KB; the body must stay under the server briefing limit of
  **8,000 chars** (`allow_long_body` not needed).
- **Inline-only / no-MCP worker** (`role=cursor-sdk`, API generate roles) → emit the
  full closeout **inline**. Stargate's on-behalf delivery
  (`async_tracker_delivery/on_behalf.py`) automatically writes a durable cortex
  sidecar first and sets `allow_long_body` as needed. Do **not** instruct an
  inline-only worker to "write a sidecar" — it relies on the on-behalf auto-sidecar
  (best-effort under the body limit; mandatory/terminal only when content is
  oversized, > 64,000 chars). That auto-sidecar guarantee is a property of the
  on-behalf `op=to_thread` path, **not** a blanket guarantee for a worker posting
  its own turn.

**Bus limits (server-enforced, `libs/agent_bus_store/turns_models.py`):** **8,000**
chars without `allow_long_body`, **64,000** with it. "≤2 KB pointer" is a
**discipline target** (keep pointers far under the limit), NOT the enforced
threshold. `allow_long_body=true` is the sanctioned exception only when a recipient
genuinely needs inline long-form > 8K **and** a sidecar would break the
communication contract; the default stays brief body + sidecar.

This per-packet author default is distinct from the packet-level "≤25-line pointer,
packet stays on disk" delivery discipline (§ Naming + delivery). Guidance only — no
admission-lint enforcement (the inline-only sidecar is already enforced in the
delivery layer).

## Skeleton

```
---
contract: consult   # or implement — explicit authority grant (optional; MCP contract= overrides)
---
<scope>
Goal: <one-line>. Selection mode: <targeted | branch | path>.
Primary artifacts: <paths>.  Out of scope: <...>.
</scope>

<invariants>
Read before editing:
- fs(cortex, agent-skills/architecture-invariants.md) — universal tag index
- fs(cortex, agent-skills/ulg-architecture.md) — ULG tag index (when ULG)
- fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc) — six-block contract
Per-task narrowing (tag | rule — reason):
| Tag | Rule |
|---|---|
| [universal:no-bc] | delete old surfaces; update consumers same change |
| [scope] | every changed line traces to task |
| [quality] | SLOC ≤400/≤300; load quality-gates.md on code change |
| [ulg:service-ops] | manage MCP only; load service-ops.md on deploy |
</invariants>

<task_guidance>
<questions / phases>. For implement: ## Acceptance criteria (numbered, all required).
</task_guidance>

<corpus>
<incident / artifact / pointers>
</corpus>

<mcp_capabilities>
You have MCP. Investigate before forming findings. Cite every tool call.
</mcp_capabilities>

<output_format>
<finding shape for consult, or closeout table for implement>
</output_format>
```

## Preliminary scaffold (Composer) → densification (reasoner)

**Invariant:** ∀ packet whose mechanical blocks are templated: a cheap tier
(`composer-2.5`) MAY draft the **scaffold**; the reasoning tier (web-claude /
cursor-consult / Opus) **densifies** the judgment-bearing blocks. The dense,
dispatch-ready artifact's authorship stays with the reasoner — this does NOT
invert the § Dispatch lifecycle invariant, because the scaffold carries structure,
¬ conclusions.

Gate (per block): scaffold iff the block is low-judgment; densify iff it carries
design judgment a wrong draft could **anchor**.

Verification posture for densification: the preliminary todo/spec/packet is a
fallible candidate, not authority. The reasoner must re-derive the task from
primary artifacts before accepting the scaffold's claims, then correct or discard
wrong framing instead of elaborating it into the dense artifact.

| Block | Scaffold (Composer) | Densify (reasoner) |
|---|---|---|
| `<scope>` | path list, git SHA, selection mode | — (mechanical) |
| `<corpus>` | changed-file manifest (paths only) | — (mechanical) |
| `<invariants>` | skill-ref lines, SLOC/no-bc boilerplate | per-task narrowing |
| `<output_format>` | finding/closeout shape boilerplate | — (mechanical) |
| `<task_guidance>` | section headers / question stubs | **all judgment** — questions, criteria, acceptance |
| `<mcp_capabilities>` | tool list boilerplate | evidence-format specifics |

**Anchoring caveat:** the pattern is upside-only where the scaffold is structural.
Where a preliminary artifact would embed a *design decision* (e.g. proposed module
boundaries), a cheap draft can anchor the reasoner into elaborating a flawed
structure rather than reasoning fresh. There, prefer either/or tiers with
escalation, ¬ scaffold→densify chaining — unless the draft is explicitly labeled
"candidate, re-derive don't elaborate." See `.cursor/commands/overhaul.md` § 2
(split planning) for the worked exclusion.

## Naming + delivery

- **Light execution (default):** `team_dispatch(op=generate, role=cursor-sdk, source_ref=todo:{slug}, contract=implement)` runs **Composer automatically** on a bus thread — **no** `cursor-implement` handoff or IDE pickup. Same model tier as manual handoff; different transport. **`contract=implement`** preferred; `light-bounded` / `pure-mechanical` when narrower. Attributes MUST be distilled on the todo. `cursor-implement` handoff is operator-attended **fallback**, not a peer default. Full policy: `agent-skills/consult-routing.md` § Implement lane — source_ref.
- **Web Gate-2 closeout:** distill attrs on the todo, then **cursor-sdk generate** (above) — do not route implement back to “operator open Cursor thread” unless SDK is ineligible.
- **Legacy / escape-hatch:** hand-authored materialized implement packet at `tmp/reviews/<task>-<seat>-packet.md` + `packet_path=` on generate, or `packet_path=` on handoff consult briefs — write the file **before** the dispatch call.
- **`packet_path` root** (legacy hand-authored paths only): Stargate resolves `packet_path` relative to `PROJECT_ROOT`
  (`/mnt/torus/projects/universal-llm-gateway`). Use `tmp/reviews/<file>.md` — **no repo prefix**.
  `fs(sandbox="workspaces")` uses a different root (`/mnt/torus/projects`) and needs the
  `universal-llm-gateway/` prefix. These are different; conflating them gives `handoff_packet_missing`.
- **Web handoffs (`web-consult` / `web-implement`):** complete § **Web-receiver priming checklist**
  above before dispatch. The server `build_pointer_body` arch-layer reminder (consult) is a
  backstop only — it does not replace the checklist.
- **Cursor + cursor-sdk arch-layer note** (when target is `claude-cursor` or `cursor-sdk`
  implement via `team_dispatch(op=generate, role=cursor-sdk, …)`): include Block 2
  skill-refs for `architecture-invariants` + `ulg-architecture`, task narrowing, and Block 5
  item 0 arch-layer reads — **unlike web**, where generic boot does not inject CODING-scope
  arch bodies and ULG-touching consults must wire the arch pair in Block 2 (§ Web-receiver
  priming checklist). Both cursor seats load project rules via `setting_sources=all`, but only
  the engineering-discipline layer (`alwaysApply: true`)
  auto-attaches; the arch layer (`topology_ws`, `mcp-integration_ws`, `event-debugging_ws`, …)
  is description-gated and does NOT reliably attach in a packet-booted thread, so Block 2
  skill-refs (`architecture-invariants`, `ulg-architecture`) and Block 5 item 0 (`fs(cortex, …)`
  reads before findings/edits) are **load-bearing**, not a backstop. **cursor-sdk** also
  receives a mechanical implement preamble enforcing these reads — packet authors must still
  include skill-refs and narrowing so the executor loads task-specific cortex skills beyond
  the universal pair. Only genuine trim: ¬ re-inline the auto-loaded engineering-discipline
  layer (SLOC/modularization, code style, scope, `no-bc`, logging).
- Only a ≤25-line pointer is posted to the bus; the packet stays on disk.

## Authority

| Topic | Source |
|---|---|
| Six-block contract | project `.cursor/rules/architecture-handoff-protocol.mdc` |
| Dispatcher matrix | project `.cursor/rules/handoff-dispatchers.mdc` |
| Transport routing | `agent-skills/consult-routing.md` (cortex) — §Dispatch lane |
| Dispatch metadata on todos | `universal-llm-gateway/.cursor/rules/todo_ws.mdc` §Dispatch metadata |
| Admission lint | `services/universal-stargate/systems/frontier_consult/handoff.py` |
