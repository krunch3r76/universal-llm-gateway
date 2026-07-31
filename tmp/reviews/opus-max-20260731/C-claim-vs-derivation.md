# C — Claim versus derivation

**Supersedes** the prior pass at this path (committed `888ccf48`, 149 lines). What
survives is marked; where I disagree it is stated as disagreement, not silently
dropped. The prior pass's assertion-site inventory is largely correct and I keep
it. Its central proposal — a `ProvenancedValue` type carrying
`observed | derived | asserted` — I reject as the primary remedy, for reasons in
§"Why not schema-level provenance".

---

## Question

**Which fields in ULG's envelopes are observed and which are asserted, and can
that distinction be made visible at the schema level?**

Unchanged in words. Sharpened in reading: the question asks whether a
distinction that *does not exist today* **should**, and what making it
structural would cost. That is an architecture argument. An inventory that
proposes provenance on everything has answered the easy half and skipped the
part that requires a judgment — which sites should keep asserting, and whether a
gate is the right instrument at all.

---

## Verdict

**The distinction should be structural. "Structural" means control flow and type
shape — not a provenance field on a schema.**

Schema-level provenance is the wrong instrument, and the prior pass reached for
it because it was the available move. A `basis: observed | asserted` field is
*filled in by the producer*. The producer that writes `status: executed` without
running a probe is exactly the producer that will write `basis: observed`
without running a probe. It adds a field, a migration and a consumer contract,
and relocates the discipline problem one level up. **Provenance-as-data is
discipline with extra steps.**

What actually worked in this codebase — twice, today — carried no provenance
annotation:

- `_disposition_for` (`services/git_integration_worker/cursor_auto/handler_propagation.py:194-204`)
  derives the envelope disposition as a total function of `executions[]`,
  flooring to the weakest row. There is **no code path** that produces the outer
  value independently. `propagated` over a failed row is not *labelled* wrong,
  it is unrepresentable.
- `settle_open_row` (`libs/charter_runner_store/propagation_terminal.py:96`)
  makes an observed probe the only route to a terminal state, and gives
  "I don't know" first-class returns (`deferred`, `unsettled`).

Both make the wrong claim **unrepresentable** rather than **annotated**. That is
the difference between a type and a comment. So the principle is not "record
where a value came from":

> **Remove the code path by which a value can be produced without its evidence.**

Three forms, in descending order of how well they earn their cost:

- **S1 — Derive, don't assert.** An aggregate gets exactly one producer: a total
  function of its constituents. (`_disposition_for`.) No gate, no field, no
  runtime cost. This is the strongest move available and it is underused.
- **S2 — Make "I don't know" cheap and terminal-safe.** Every predicate
  answering a question about the world is three-valued, and the unknown branch
  must not reach a terminal state. Most false claims in this system are a
  boolean standing where the honest answer was *no reading*.
- **S3 — Gate, but only where the claim has no natural falsifier.** That is the
  incapacity direction, and only there. One advisory detector, argued in
  §"The ungated asymmetry".

S1 and S2 are not gates and have no false-positive surface. If the reader
accepts nothing else here, they should accept those two, because they cost
nothing to route around — there is nothing to route around.

---

## What I found

### 1. The specimen reproduces, and the second instance was made by code

The prior pass wrote, in its own **PROPAGATION REQUIRED** section, that
`git_integration_worker` was landed-not-live at `4f7367ff`. False at the time of
writing. I re-verified independently rather than trusting the withdrawal:
`git merge-base --is-ancestor 4f7367ff 82f07260` → true, and
`GET http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness` returns
`{"live":true,"code_version":"82f07260…","uptime_s":4243}`. The fix was live. The
falsifying observation was one `curl` away, in the section of the document whose
subject is exactly this error.

That is one worker's slip, n=1, and the brief is right to ask whether it
generalises. **It does, and the second instance was not made by a mind.**

The propagation ledger at `/home/io/.local/share/charter-runner/root-ledger.sqlite`
currently holds **7 rows in terminal `failed` state** for
`git_integration_worker`, all with `defer_reason = code_version_mismatch`. All
seven were settled against a **single probe**: identical
`observed_code_version = 3e6ca550a1ac`, `uptime_s` values spanning 929.565 →
929.649 — one sweep, one instant, seven distinct `code_ref` targets. At most one
target could have matched any single observation; six were arithmetically
guaranteed to "mismatch."

**Six of those refs are ancestors of the running `82f07260`.** The code each row
demands is live right now. The ledger permanently records failure for
propagations that demonstrably succeeded, and `fail_row` transitions only
`WHERE status='open'` (`libs/charter_runner_store/propagation_ledger.py:207`),
so the rows have left the open set and no later probe will ever revisit them.

That is the thesis in a production database: a false negative that no
observation can reach, because the mechanism that would correct it only looks at
rows the false negative removed.

I cannot prove which caller produced those seven rows and do not claim to. What
the payloads establish: a single probe of a process with 929s uptime — i.e. not
a freshly restarted process — drove seven terminal verdicts. Under the code as I
found it this morning, an unguarded sweep produces exactly that shape.

### 2. The guard nominated as the model has the defect at its leaf

The brief names `propagation_terminal.py` as "the shape to emulate; say what
makes it right." **Its control flow is right and its leaf predicate is wrong**,
and the split is instructive.

Right: `settle_open_row` has a three-valued outcome, an unreachable probe defers
rather than concludes, and a probe suspected of being the outgoing process
defers. Deferral is representable, cheap, and not penalised.

Wrong: `_probe_is_outgoing_generation` (`:31-41`) reads `payload["uptime_s"]`
and returns `False` when it is absent — *"I could not tell"* collapsed into
*"no, not outgoing"*, and "no" here licenses a terminal failure. Three ways that
bites:

- **Composite payloads are invisible to it.** `probe_for_row` returns
  `{"mcp_health": {...}, "cortex_api": {...}}` for client-visible `mcp` rows
  (`services/git_integration_worker/cursor_auto/propagation_probe.py:83`). No
  top-level `uptime_s`, so the guard **cannot fire** for those rows. Not
  hypothetical: the ledger holds 4 open and 2 closed `mcp`/`client_visible` rows.
- **A half-unreachable probe reads as a mismatch.** `proof_observed` returns
  `False` when either half of the composite fails to answer
  (`propagation_probe.py:98`). That `False` flowed into the `fail_row` branch
  labelled `code_version_mismatch` — a mismatch that was never observed.
- **No boundary meant no check at all.** `reconcile_all_open_rows` passed no
  `settle_not_before_monotonic`, so the guard was skipped outright.

The common shape: **absence of the information needed to rule out a wrong
referent was treated as permission to conclude.** That is the thesis in one
line, sitting inside the file held up as the model.

### 3. The claim-vs-observation gates that exist all point one way

`libs/cortex_store/dispatch_ops/ops_audit_detectors.py:195` registers
**48 detectors** (counted, not estimated). Those that check a claim against an
observation:

| Detector | Polices |
|---|---|
| `landed_claim_not_on_master` (`_detectors/git_reconcile.py:112`) | "I landed X" vs the actual git graph |
| `confirmed_entity_no_assertions` (`_detectors/auditor.py:23`) | `confirmed` without backing assertions |
| `confirmed_attribute_no_assertion` (`_detectors/auditor.py:81`) | confirmed attribute with no supporting claim |
| `done_entity_unsubstantiated_band_mismatch` | done-ness vs substantiation |
| `provenance_cites_staging` | citation pointing at non-canonical source |

Every one polices an **affirmative** claim. Filtering all 48 registered kinds
for negative-capability tokens (`incapac`, `unavail`, `cannot`, `unsupported`,
`capab`, `denial`, `impossible`) returns **zero**.

### 4. Sites where the current shape is already correct

Worth recording so the remedy does not sprawl onto them.
`classify_capture_status` (`services/git_integration_worker/cursor_sdk_capture_status.py:376-393`)
returns `unavailable` only from an observed condition (`baseline is None`), and
scopes it to *capture completeness* rather than to the world. `_execute_row`
(`handler_propagation.py:128-145`) falls through to the probe when `manage`
returns an unrecognised status — deferring to observation rather than to an
unparsed claim, which is the right instinct.

---

## What I changed

**`4aae67b2`** — *fix(propagation): a non-answer must not terminally fail a ledger row*

- **New** `libs/charter_runner_store/propagation_determination.py` (111 lines).
  Separates *reading* a probe from *deciding* on it. `classify_probe` returns
  `matched | contradicted | indeterminate`; `observed_code_versions` understands
  both flat and composite payloads and returns `None` when nothing readable came
  back; `outgoing_generation_ruled_out` demands **both** a restart boundary and a
  present `uptime_s`, so absence of either means *not ruled out* rather than
  *ruled in*.
- **`propagation_terminal.py`** — a contradiction is terminal only when the
  incoming generation is positively ruled out. Otherwise the row stays open with
  a defer reason naming why (`proof_contradicted_generation_unverified`,
  `proof_indeterminate_probe_unreadable`). `reconcile_all_open_rows` can now
  forward a restart boundary. Pre-existing `I001` cleared.
- **`test_propagation_terminal.py`** — three new regression tests; two existing
  tests updated because they **pinned the behaviour I am calling a defect**
  (`test_queued_row_fails_on_mismatched_probe`, now
  `test_unguarded_mismatch_leaves_row_open`; and the `failed == 1` expectation in
  `test_reconcile_before_after_counts`). Flagged loudly rather than quietly
  amended — a reviewer should check this specific judgment.

**A match remains sufficient on its own, deliberately.** Observed equals target
is self-validating whoever answered; a non-match is not. The two directions get
different treatment because their epistemics differ, which is the same
asymmetry as §"The ungated asymmetry" at instruction scale.

Verification (quoted):

```
libs/charter_runner_store/test_propagation_terminal.py      12 passed, 2 warnings in 1.16s
libs/charter_runner_store/ + test_handler_propagation_disposition.py   26 passed, 2 warnings in 1.23s
scripts/model_manager/ui/controller/tests/test_git_worker_drain_supervisor.py    7 passed in 0.52s
ruff check (charter_runner_store, handler_propagation, drain supervisor)  All checks passed!
python -m compileall -q libs/charter_runner_store/                        OK
```

One process note, recorded because it is the same failure class in miniature: a
directory-wide `ruff check --fix` silently rewrote `libs/charter_runner_store/db.py`,
which my task did not touch. I confirmed the change was mine (HEAD's `db.py`
raises the matching `I001` under `--isolated --select I`) rather than assuming,
and reverted it. Only three explicitly named paths were committed.

---

## Sites that should remain assertions

The line I draw:

> **Observation is mandatory only where a bounded probe exists whose referent is
> the same as the claim's referent. Everything else is legitimately asserted, and
> demanding evidence for it degrades the signal rather than improving it.**

Three classes stay assertions.

**(1) Constitutive, not discovered.** `execute_multi_op_unsupported`
(`cursor_auto/execute_admission.py:144`), `nested_in_seat_unsupported`
(`cursor_auto/gate_serialize.py:19`). These are rules the system *enacts*, not
states it *finds*. There is nothing to probe; the refusal **is** the policy.
Demanding evidence is a category error and actively harmful — it makes a
constitutive refusal look contingent, therefore retryable, inviting exactly the
behaviour the rule exists to prevent. The prior pass's `policy_denial` is right
here and I keep it, but as a **distinct token**, not as one value in a
provenance enum: its whole job is to be *not* on the same axis as observed and
asserted.

**(2) Judgment.** Root-cause attributions, design preferences, "discipline is
the right level here". No probe settles them. Forcing them into an `observed`
basis would be a worse falsehood than leaving them plainly asserted.

**(3) Scoped machine output about the observer, not the observed.**
`capture_status=unavailable`, `lint_unavailable`
(`cursor_sdk_closeout.py:251`), `i2_queue_unreachable`
(`propagation_probe.py:55`), `events_query_unavailable`
(`ulg_story_projector/projector.py:138`), `baseline_unavailable`
(`cursor_sdk_revert.py:117`).

**This is where I disagree with the prior pass most sharply.** These values are
already honest and already derived from an observation. Stamping them
`basis: observed` would be *actively harmful*: it certifies them as observed —
which they are — and thereby strengthens precisely the wrong inference, the
reader's promotion of *"we did not see"* into *"there was nothing."*

**Their failure mode is scope, not provenance, and the two are orthogonal axes.**
The relay fixture the prior pass itself cited — `files_modified=[]` beside
`capture_status=partial` — is two honest, correctly-derived fields that a reader
conflates. No provenance annotation touches that error. The remedy is naming: a
field whose name cannot be read as the stronger claim. `capture_status` is
well-named. `files_modified` is not; it should be `files_observed_modified`, or
the empty list should be unrepresentable when capture is not `complete`.

The prior pass wrote "the schema must preserve that scope" and then proposed a
provenance field, which does not preserve scope. That is the substantive
correction.

**Two tokens in my own territory fall in class 3 and I left both alone.**
`STATUS_COMPLETED` after SIGTERM returns
(`scripts/model_manager/ui/controller/git_worker_drain_supervisor.py:264`) is an
honest control-plane fact whose token invites a data-plane reading. The prior
pass's `restart_signal_sent` / `live_proof` split is correct and I endorse it —
noting it is a **scope** fix, my category, not a provenance fix. Likewise
`_ENVELOPE_FROM_ROW["submitted"] = "propagated"`
(`handler_propagation.py:158`): a merely-submitted row reports the envelope
token `propagated`. The summary prose is honest ("ledger row open until proof
closes"); the token is scope-leaky. I did not change either, because both are on
wire contracts with consumers I do not own and a rename without a consumer sweep
trades one silent defect for another.

---

## The ungated asymmetry

**Gates exist against overclaiming success. Nothing gates claiming incapacity.
The fleet has spent its entire gate budget on the direction that corrects itself
and none on the direction that cannot.**

The reason this is not merely uneven but *inverted*:

- A false **success** claim has a natural falsifier. The next seat that tries to
  use the thing finds it missing, and the claim dies on contact. The gate is
  largely **redundant with reality**; it buys earlier detection of something
  reality would have caught anyway.
- A false **incapacity** claim has **no** natural falsifier, because the
  falsifying act is precisely the act it suppresses. "X cannot be done from
  here" removes the trial that would refute it. It does not decay. It compounds:
  each seat that reads it and does not try adds apparent corroboration.

So the fleet's 48 detectors are aimed at the self-correcting direction, and the
self-sealing one is unpoliced. Three false incapacity claims today, each
suppressing later attempts.

**The brief is itself a specimen.** §0's PATH CORRECTION is the operator
hand-patching, in prose, the absence of this gate — pre-emptively defending
against a false incapacity claim that the brief's own wrong path would otherwise
have generated ("Do not conclude the corpus is unreachable on the strength of
that path failing"). When an author must spend a paragraph per document to
prevent a defect class, the defect is structural and the prose is a workaround.

**Feasibility is settled, not speculative.** `detect_landed_claim_not_on_master`
already implements the whole pattern: extract a token from a claim, probe an
authority, emit a finding — with exactly the right false-positive posture
("worker-unreachable (advisory, never blocking)", `git_reconcile.py:123`). The
mirror detector is a modest amount of work on a proven template. There is no
capability gap here; there is an attention gap, and the attention went to the
direction that was already self-correcting.

### The one gate I endorse

**`incapacity_claim_unfalsified`** — advisory, session-close scoped, non-blocking,
exactly like `landed_claim_not_on_master`.

Fires on an assertion at `confidence: confirmed` whose predicate class is
negative-capability, and which lacks any of:

- ≥2 attempts recorded with **distinct** `source_ref`s (two failures of the same
  route are one observation, not two),
- a named alternative route checked and its result,
- an `expires_at`.

**The gate does not verify the incapacity, and this is the point, not a
weakness.** Verifying "X is impossible" is unbounded and no gate can do it. It
checks the **shape of the evidence offered** and forces an expiry, so the claim
**decays instead of persisting**. A cheap bounded check on an expensive unbounded
claim. It earns its cost not by catching the error but by ensuring the error
stops being load-bearing.

**Its precondition is the real deficit.** The gate needs something structured to
bite on, and today incapacity claims have **no type at all** — they are
indistinguishable from any other assertion, which is why zero of 48 detectors
can see them. So the actual structural proposal, upstream of the gate, is:
**give incapacity claims a type.** A `CapabilityAssessment` carrying `scope`,
`attempts[]`, `alternative_route_checked`, `expires_at` — which is the one part
of the prior pass's proposal I keep wholesale, and I keep it precisely because
it is *not* a provenance annotation on an existing value. It is a new kind of
claim that did not previously exist, for a claim type the system currently
cannot represent.

---

## The case that discipline, not structure, is the right level

The brief permits this conclusion. It deserves a real steelman, because parts of
it are correct and survive into my answer.

**Steelmanned:**

1. **Gates cost more than they appear to, and this registry proves it.**
   `implement_ready_spec_unvalidated` sits **disabled** in
   `ops_audit_detectors.py:238` with a comment recording that it false-positived
   *every* `implement_ready`. A gate that cost more than it caught, in the exact
   file where the new gate would go. Every gate is a false-positive generator, a
   maintenance surface, and something to route around.
2. **Incapacity claims are prose, and prose does not have a SHA.**
   `landed_claim_not_on_master` works because it extracts a closed,
   machine-checkable token. "X cannot be done from here" offers no such handle. A
   regex over `cannot|unavailable|unreachable` fires on quoted text, on
   constitutive policy denials, and on well-scoped machine tokens like
   `capture_status=unavailable` — i.e. on all three classes I just argued must
   remain assertions. The gate's first act would be to attack its own exemptions.
3. **The fleet already has three always-on rules on exactly this subject** —
   `provenance-discipline`, `presence-discipline` P3,
   `completion-provenance-discipline`. Adding a 49th detector to a system with
   three standing rules on the same defect suggests the binding constraint is not
   the absence of a check.
4. **The strongest point, and it is against me: my own fix is discipline.** No
   gate produced `4aae67b2`. A seat read the code, formed a hypothesis, and
   checked it. Every finding in this document was produced by the very faculty
   the document argues is insufficient.

**Answered:**

On (1) and (2) I largely concede, and they are why I endorse **one advisory
detector** rather than a type system, and why the detector must not attempt
semantic classification of prose. It fires on the **structured** part — an
assertion at `confidence: confirmed` with a negative-capability predicate class —
and checks evidence shape, not truth. If the fleet declines to type its
incapacity claims, the gate has nothing to bite on and discipline is indeed the
only available level. That is why "give incapacity claims a type" is the
proposal and the gate is a consequence of it, not the reverse.

On (3): three standing rules on this subject, and the defect fired anyway, in a
document written *about* the defect. That is evidence about the rules, not for
them.

On (4), the decisive answer is the pair of specimens, and they cut in the same
direction from opposite substrates:

- **From a mind:** the prior pass wrote a false landed-not-live claim in the
  PROPAGATION REQUIRED section of the document auditing false landed-not-live
  claims, having just enumerated this exact failure class, with the falsifying
  `curl` one command away. If maximum attention plus a written inventory of the
  defect does not prevent the defect, discipline is not the binding level.
  Discipline failed at its own best moment.
- **From a machine:** six ledger rows terminally marked failed for propagations
  whose code is live right now. **No amount of seat discipline could have
  prevented those** — they were produced by code, in a sweep, with no seat in the
  loop. Discipline cannot reach that class at all. Structure can, and did:
  `4aae67b2` closes the path.

That second datum is what makes this more than n=1. The first shows discipline
failing where it was strongest; the second shows a whole class discipline cannot
touch.

**But the concession is real and bounds the verdict.** Discipline *is* the right
level for classes 1–3 above — constitutive refusals, judgments, and scoped
observer output. I propose no structure there, and a proposal that did would be
worse than nothing. The structure-versus-discipline line is not a global verdict;
it tracks a single test: **does a bounded probe exist whose referent matches the
claim's referent?** Where it does, remove the path that bypasses it. Where it
does not, discipline is all there is, and adding a gate degrades signal while
buying nothing.

---

## Dependency on Workstream A

**Independent. Composes; does not depend.** I reject the prior pass's framing
that C is downstream of A — that framing made C unstartable and would not have
fixed anything.

- `libs/openapi_mcp/binding.py` binds **op → route identity**
  (`x-mcp: {tool, op, readonly}`). It says nothing about response bodies, and A's
  stated scope excludes changing tool semantics.
- More fundamentally: **OpenAPI describes what a service emits; it cannot make a
  producer observe anything.** A schema field asserting a value is `observed` is
  only as true as the producer filling it in. Routing the answer through A would
  have deferred C behind another workstream *and* delivered a weaker remedy.
  `4aae67b2` needed nothing from A.

**Where they genuinely compose, and it is worth flagging to A:** the `readonly`
flag on `x-mcp` is the same move I am advocating one level up — a property
*derived* from schema replacing a hand-curated allowlist requiring human
ratification per row (brief §3-A). That is S1 at the tool-surface level; my S1/S2
are its response-side analogue. The natural follow-on, if A lands `x-mcp`, is an
`x-mcp: {probe: …}` key naming the endpoint that settles a claim about a service —
which would let the hand-written per-service ladder in
`propagation_probe.py:65-73` (`if service == "git_integration_worker" … elif
"mcp" … elif "cortex_api"`) be derived rather than maintained. Non-blocking in
both directions.

---

## PROPAGATION REQUIRED

Every entry below is **observed**, not inferred from commit recency. The prior
pass's error at this exact heading is the reason for the discipline.

| Service | Needs restart at | Evidence |
|---|---|---|
| **model_manager / manage** (pid **765684**) | **`4aae67b2`** | **YES.** Sole non-test importer of `propagation_terminal` is `git_worker_drain_supervisor.py:387` (verified by ripgrep across the tree), which runs in this process. `ps` shows start **Fri Jul 31 11:24:04 2026**; `4aae67b2` is dated **2026-07-31 14:07:03 -0700**. Process predates the commit and Python does not hot-reload. `/proc/765684/cwd` → `/mnt/torus/projects/universal-llm-gateway`, confirming it loads from this checkout. |
| **second `scripts.model_manager.ui`** (pid **669567**) | `4aae67b2` | Started **10:43:41** from a *cursor-dispatch-home* venv (`…/cursor-dispatch-homes/auto-9066617448d4-home/.venvs/universal`), cwd also this checkout. Also predates the commit. **Flagged, not diagnosed** — I do not know whether a second instance should be running at all; someone with `manage` should decide before restarting blindly. |
| **git_integration_worker** | — | **NO restart required for `4aae67b2`.** GIW does not import `propagation_terminal` (ripgrep, non-test). It imports `propagation_ledger` only (`handler_propagation.py:8-12`), which I did not modify. Running `82f07260`, observed live via `/api/v1/git/cursor-auto/liveness`. |
| **git_integration_worker** @ `4f7367ff` | — | **NO restart required — already LIVE.** Independently re-verified, not taken from the prior pass's withdrawal: `git merge-base --is-ancestor 4f7367ff 82f07260` → true; liveness reports `code_version 82f07260`, `uptime_s 4243`. |

**Data-plane follow-up, not a restart.** Six terminally-`failed` rows in
`root-ledger.sqlite` are false and unreachable by any future probe (`fail_row`
only transitions `WHERE status='open'`). My fix prevents new ones; it does not
repair these. They need a one-off reconciliation that re-opens rows whose
`code_ref` is an ancestor of the service's current `code_version`. I did not
write it: it mutates operator-visible durable state on a shared host while
parallel workers are running, and it should be a deliberate, reviewed act rather
than a side effect of an investigation.

---

## Open questions and residuals

- **Literal non-SHA `code_ref`s are still mis-typed as mismatches.** One failed
  row carries `code_ref = "working"`. `_is_literal_head` special-cases only
  `"HEAD"` (`propagation_terminal.py:55`), so `"working"` reaches the comparison
  and is reported as a version mismatch — a comparison that was never meaningful.
  **My fix does not close this**; it is a distinct sub-class. Settle by widening
  the uncheckable-ref predicate to any `code_ref` that does not `rev-parse` to a
  commit, and returning `unsettled`, never `failed`.
- **Who wrote the seven rows?** The payload shape (one probe, 929s uptime, seven
  targets) is consistent with an unguarded sweep and inconsistent with
  post-restart verification, but the caller is not recorded. Settle by adding the
  settling caller to the proof payload — cheap, and it would make this class
  self-diagnosing next time.
- **Is `STATUS_COMPLETED` read anywhere as "new code is live"?** Inherited from
  the prior pass, still open. Settle by tracing every `RestartIntentStore` reader.
  It gates whether the `restart_signal_sent` / `live_proof` rename is cosmetic or
  load-bearing.
- **Would `incapacity_claim_unfalsified` have caught the three false incapacity
  claims today?** I have not tested it against them, and the honest answer is
  that it depends entirely on whether those claims were typed. Settle by
  replaying the three against a prototype detector before building it; if it
  catches zero, the gate is not worth its cost and the discipline position wins
  on this specific instrument even though the asymmetry argument stands.
- **Does the `mcp` composite path have live open rows that would now defer
  forever?** Four `mcp`/`client_visible` rows are open. Under my change a
  persistently half-unreachable probe leaves them open indefinitely rather than
  failing them. That is the intended trade — open-and-visible beats
  terminally-wrong — but it needs an operator-visible staleness signal on
  long-open rows, which `scoreboard_projection` does not currently provide.
