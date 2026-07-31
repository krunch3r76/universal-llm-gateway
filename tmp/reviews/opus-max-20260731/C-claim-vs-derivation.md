# C — Claim versus derivation

**Revision 3, post-adversarial-review.** Rev 1 (`888ccf48`) was a lower-tier
inventory. Rev 2 (`4d1bbdcf`) was my argument. This revision is that argument
after a top-tier agent from another model family was commissioned to kill it.
Five attacks landed, one glanced, two failed. The reviewer's record stands
unedited at `tmp/reviews/opus-max-20260731/C-adversarial-review.md`.

**The core recommendation survives, narrowed.** My worst error was evidentiary
and it propagated into a queued operator decision, which is corrected in
§"The operator fork" below.

---

## Question

**Which fields in ULG's envelopes are observed and which are asserted, and can
that distinction be made visible at the schema level?**

Unchanged. The reading that matters: it asks whether a distinction that does not
exist today **should**, and what making it structural would cost.

---

## Verdict (narrowed)

The rev-2 verdict was "the distinction should be structural," qualified late. The
reviewer is right that this presented a targeted runtime invariant as a general
answer to claim provenance. The honest statement is a **partition**, and
"structural" is an *enforcement placement*, not a doctrine:

| Claim class | Right level |
|---|---|
| Local, bounded, **same-referent machine facts** where a probe already exists and the bad state is terminal or self-sealing | **Structural runtime guard** — require the probe result; preserve `unknown` |
| **Cross-boundary observed values**, where direct control-flow coupling is undesirable | Direct derivation, or **framework-issued typed evidence** minted only by the probe path |
| **Policy, judgment, observer scope, untyped prose** | **Discipline** — explicit naming, expiry, read-side skepticism. No structure. |

Row 1 is where I hold ground and where attack 8 failed. Asking every future
author and reader to remember a rule is strictly weaker than making a terminal
transition require a positive determination: a norm applied at code review is
once-per-change, a transition guard is applied on every execution.

Row 3 is a genuine concession of territory, and it was already in rev 2 as the
three exempt classes. The reviewer's contribution is that once that limit is
taken seriously, this stops being a type-system campaign.

Row 2 is new, and it is the reviewer's win — see attack 2.

---

## Attack ledger

| # | Reviewer's attack | Outcome | My response |
|---|---|---|---|
| 1 | Six ancestors do not prove six successful propagations | **lands** | **Conceded, and sharpened against my own position** |
| 2 | "Provenance-as-data" rejects too broad a family | **lands** | **Conceded**, with two new specimens supporting the reviewer |
| 3 | `_disposition_for` is a narrower exemplar than claimed | **lands** | **Conceded** |
| 4 | "No discipline could have prevented" is too absolute | **lands** | **Conceded** |
| 5 | The incapacity detector is not ready to endorse | **lands** | **Conceded** — demoted to prototype-gated |
| 6 | The principle is induced from a narrow successful set | *glances* | **Does not land** — rebutted below with a third subsystem |
| 7 | `basis: observed` could rescue scoped observer output | *fails* | Reviewer concedes; scope and provenance are orthogonal |
| 8 | Discipline beats structure for referent-matched runtime facts | *fails* | Reviewer concedes the narrow class |

I also **disagree with one element of the reviewer's own strongest-case
section** — its point 2 — on sampling grounds. See §"The case for discipline".

---

## What I found

### 1. The empirical claim, corrected (attack 1)

**Rev 2 said:** the ledger "records failure for propagations that demonstrably
succeeded," because six `code_ref`s are ancestors of the running `82f07260`.

**That was wrong, and the error is a referent shift** — the same defect class the
document is about, which is now the third time this workstream has committed its
own subject matter. Ancestor reachability answers *"does the currently running
descendant contain this commit?"* The row purports to answer *"did this restart
produce the required live generation at that time?"* A later successful restart
to a descendant makes an ancestor live regardless of whether an earlier attempt
failed. Ancestry cannot establish historical success.

**What the seven rows do prove**, which is narrower and still indicts the old
terminalization:

- Seven rows were marked terminally `failed` within **0.083 seconds**.
- All seven payloads observed the **same** `3e6ca550a1ac` generation.
- `uptime_s` rose monotonically 929.565 → 929.649 — **seven rapid probes of the
  same already-running generation in one sweep**, not "a single probe" as rev 2
  said.
- Terminal failure was therefore assigned **before any incoming generation was
  positively identified**. The verdicts were *unearned*. That is the claim.

**And I go further than the reviewer, against my own prior framing.** The
reviewer keeps ancestry as evidence that the obligations "may now be
satisfiable." Even that is too generous. The documented proof obligation is
**exact equality** — `GET /api/v1/git/cursor-auto/liveness → code_version ==
code_ref` (`libs/implement_admission/propagation_row.py:57-59`), and for mcp
"both `code_version == code_ref`" (`:61-63`) — and the implemented predicate is
exact string equality (`propagation_terminal.py:88-93`;
`cursor_auto/propagation_probe.py:105-106`). Under the predicate as built,
ancestry satisfies **nothing**. `82f07260 != c02850aea885`.

So ancestry establishes neither historical success nor present satisfaction. It
establishes only that the code is *included* now. My rev-2 inference was not
merely over-strong; it was measuring against an obligation the system does not
implement. That observation is what generates the operator fork below.

### 2. The guard nominated as the model has the defect at its leaf (stands)

Unchallenged, and the reviewer verified it. `propagation_terminal.py`'s control
flow was right — three-valued outcome, deferral representable and cheap — while
`_probe_is_outgoing_generation` (`:31-41`) read `payload["uptime_s"]` and
returned `False` when absent: *"I could not tell"* hardened into *"no, not
outgoing,"* where "no" licensed a terminal failure. Three ways it bit:

- **Composite payloads were invisible to it.** `probe_for_row` returns
  `{"mcp_health": …, "cortex_api": …}` for client-visible `mcp` rows
  (`cursor_auto/propagation_probe.py:83`) with no top-level `uptime_s`, so the
  guard **could not fire**. The ledger holds 4 open and 2 closed such rows.
- **A half-unreachable probe read as a mismatch** (`propagation_probe.py:98`).
- **No boundary meant no check at all** — `reconcile_all_open_rows` passed none.

The common shape: **absence of the information needed to rule out a wrong
referent was treated as permission to conclude.**

### 3. A third subsystem, and it is the strongest specimen in the session

This is new since rev 2 and it answers attack 6 directly, because it is a
**failure** case in a **different** subsystem, verified over the wire.

`cortex-api` reports two different `code_version` values for the same service:

- TCP `http://127.0.0.1:8202/health` → `code_version: dba38ed7b349…`
- UDS `/tmp/universal-protocol/cortex-api.sock` `/health` → `code_version: 82f07260570c…`

They cannot both be true. **I discriminated which is false rather than only
noting the conflict:**

1. Both processes started **12:51:44** and **12:51:46** (`ps`). Commit
   `dba38ed7` is dated **14:10:28**. A process cannot run code committed
   **79 minutes after it started**. The TCP claim is false by arithmetic.
2. `dba38ed7` ("native x-mcp route stamps") **contains** `806722dd`, `76148b0f`
   and `5094288b`. Yet the served document at that same TCP surface carries
   **0 `x-mcp` stamped operations across 82 paths**. The endpoint's own OpenAPI
   falsifies its own health claim.
3. `82f07260` **lacks** all three x-mcp commits, which is exactly consistent with
   0 stamped operations. The UDS value is the credible one.

**Why this matters more than the ledger case.** `code_version` is not an
incidental field — it is *the* field the documented proof obligation consumes
(`code_version == code_ref`). The entire propagation discipline rests on it. And
it is **self-reported**, with at least one implementation apparently deriving it
from the *checkout* rather than from the *running process* — two different
referents, which is the same defect as everything else here, sitting under the
mechanism meant to detect it. Flagged as hypothesis on mechanism (a separate
worker owns the fix); the falsity itself is established by legs 1–3 above.

This is a same-referent, machine-probeable, bounded-probe claim — row 1 of the
partition, dead centre.

### 4. Sites where the current shape is already correct (stands)

`classify_capture_status` (`cursor_sdk_capture_status.py:376-393`) returns
`unavailable` only from an observed condition (`baseline is None`) and scopes it
to capture completeness rather than to the world. `_execute_row`
(`handler_propagation.py:128-145`) falls through to the probe on an unrecognised
`manage` status, deferring to observation rather than to an unparsed claim.

---

## Why not self-reported provenance (narrowed — attack 2)

**Rev 2 said** "provenance-as-data is discipline with extra steps." Too broad.
The reviewer is right that this defeats only a **bare producer-filled enum**:

```text
value = "executed"
basis = "observed"     # two self-reported claims, not one claim plus evidence
```

It does **not** defeat a **framework-issued** observation value — an `Observed[T]`
whose constructor is reachable only through the probe path and which the terminal
transition *requires* as an argument. That is type shape and control flow, and it
is schema-visible. The dichotomy "control flow **or** provenance field" was
false.

**Corrected claim:** reject **self-attested provenance enums**, not
schema-visible provenance categorically.

**Two specimens from this session support the reviewer, not me**, and I record
them because they are the reason the narrowing is right rather than merely
conceded:

- `stamp_fastapi_routes` (`libs/openapi_mcp/binding.py:114-145`) returns a count
  of routes stamped. Workstream A reports this FastAPI version keeps included
  routers lazy, so it would stamp **0 of 20** and return `0` as a *successful*
  result. **Nothing in the tree asserts on its return value** — verified: the
  only occurrences of the symbol are its definition and its export. A
  self-reported success integer with no evidence behind it, in code rather than
  prose, silent because unexercised.
- `cortex-api`'s `code_version` above — a self-reported observation, false, in
  the field the propagation contract treats as proof.

Both are self-report failing where a framework-issued value could not have. That
is the reviewer's point, made by the codebase.

**Where this composes with A:** a probe adapter that mints the only `Observed[T]`
constructor, surfaced in the response schema, would put row 2 of the partition on
a real footing. `x-mcp` shows the mechanism for *route identity*
(`binding.py:36-80` derives from the served document and rejects malformed or
duplicate bindings) but carries no response provenance and cannot establish that
a probe ran. So A remains a composition, not a dependency.

---

## The `_disposition_for` exemplar, narrowed (attack 3)

Rev 2 used `_disposition_for` as an exemplar of making a value "unrepresentable
without its evidence." The map contains `"submitted" -> "propagated"`
(`handler_propagation.py:155-160`).

**Corrected:** it makes **`propagated` over a *failed* execution row**
unrepresentable. It does **not** make **a propagated-looking token without
proof-of-live** unrepresentable. Rev 2 noted this later as a scope leak, which
contradicted the breadth of its own headline exemplar. The reviewer is right that
this is not just wording: **structure can faithfully enforce a semantically bad
mapping**, exactly as the old `propagation_terminal.py` had good control flow
around a bad leaf predicate.

So the general slogan "remove the code path by which a value can be produced
without its evidence" is replaced by a **per-site testable invariant**. For this
envelope:

> Outer status is a total function of row statuses, **and** unproved submission
> carries a token that cannot be read as live completion.

The first conjunct holds today. The second does not. I still have not changed it,
for the rev-2 reason: it is a wire token with consumers I do not own, and whether
any consumer reads `propagated` as proof-of-live is an open sweep.

---

## Sites that should remain assertions (stands; attack 7 failed)

> **Observation is mandatory only where a bounded probe exists whose referent is
> the same as the claim's referent.**

**(1) Constitutive, not discovered.** `execute_multi_op_unsupported`
(`cursor_auto/execute_admission.py:144`), `nested_in_seat_unsupported`
(`cursor_auto/gate_serialize.py:19`). Rules the system *enacts*, not states it
*finds*. Nothing to probe; the refusal **is** the policy. Demanding evidence
makes a constitutive refusal look contingent, therefore retryable — inviting the
behaviour the rule exists to prevent. `policy_denial` belongs here as a
**distinct token**, deliberately *not* a value on the observed/asserted axis.

**(2) Judgment.** Root-cause attributions, design preferences, "discipline is the
right level here." No probe settles them.

**(3) Scoped machine output about the observer, not the observed.**
`capture_status=unavailable`, `lint_unavailable` (`cursor_sdk_closeout.py:251`),
`i2_queue_unreachable` (`propagation_probe.py:55`), `events_query_unavailable`
(`ulg_story_projector/projector.py:138`), `baseline_unavailable`
(`cursor_sdk_revert.py:117`).

**Attack 7 tried to rescue schema provenance here and failed; the reviewer
concedes the point.** Marking `files_modified=[]` as `observed` says how the list
was produced, not whether the observer had complete coverage — it would make the
misreading look **better founded**. Scope and provenance are orthogonal axes, and
the failure mode here is scope.

The reviewer strengthened this with code I had not cited: the capture layer
already documents that `effects` is authoritative **only** when
`capture_status=complete` (`cursor_sdk_capture_status.py:396-411`), and the relay
warns that an empty machine capture is not authority
(`cursor_auto/closeout_relay.py:181-185`). The contract exists in comments; the
type does not enforce it. The repair is a scope-bearing sum type or a name like
`files_observed_modified`, plus an **unrepresentable** empty-authoritative state
when coverage is degraded.

`STATUS_COMPLETED` after SIGTERM returns
(`scripts/model_manager/ui/controller/git_worker_drain_supervisor.py:264`) is
class 3: an honest control-plane fact whose token invites a data-plane reading.
The `restart_signal_sent` / `live_proof` split is a **scope** fix. Unchanged, for
the consumer-sweep reason.

---

## The ungated asymmetry (finding stands; instrument demoted)

**Corrected count:** `get_all_detectors()`
(`libs/cortex_store/dispatch_ops/ops_audit_detectors.py:195`) registers
**49** detectors, not 48. Rev 2's regex undercounted by one; I re-counted by AST
and confirm 49, and confirm **zero** detector names match
`incapac|unavail|cannot|unsupported|capab|denial|impossible`.

The asymmetry holds, and it is inverted rather than merely uneven. A false
**success** claim has a natural falsifier — the next seat that tries to use the
thing finds it missing — so those gates are largely redundant with reality. A
false **incapacity** claim has **no** natural falsifier, because the falsifying
act is the one it suppresses. It does not decay; it compounds, as each seat that
reads it and does not try adds apparent corroboration.

**The brief is itself a specimen.** §0's PATH CORRECTION is the operator
hand-patching, in prose, the absence of this gate — pre-emptively defending
against a false incapacity claim the brief's own wrong path would have generated.
When an author must spend a paragraph per document to prevent a defect class, the
prose is a workaround.

### The detector, demoted (attack 5)

Rev 2 endorsed `incapacity_claim_unfalsified`. **The reviewer is right that this
was a hypothesis dressed as a recommendation**, and rev 2 admitted in its own
residuals that the three motivating claims had never been replayed. Two further
facts I accept:

- The negative-capability predicate class **does not exist**. Assertions carry
  `derivation_type` (`direct_observation`, `agent_observation`, `inference`),
  `observed_at`, `evidence`, `evidence_uris`, optional `predicate_form`
  (`libs/cortex_store/models/assertions.py:18-97`) — so partial schema-level
  provenance *already exists*, is caller-supplied, and does not classify negative
  capability.
- `implement_ready_spec_unvalidated` sits **disabled** in that same registry
  (`ops_audit_detectors.py:238`) after false-positiving every `implement_ready` —
  a precedent against optimism, in the exact file.

**Demoted to prototype-gated.** Build only after: (1) obtain the three source
claims; (2) prototype classification and evidence-shape checks; (3) measure
catches and false positives; (4) adopt only if it catches the motivating cases
without attacking policy denials or observer-scoped output. If it catches zero,
the discipline position wins on this instrument and the asymmetry finding still
stands — those are separable, and rev 2 wrongly bundled them.

---

## The case for discipline (steelmanned, answered, with one rebuttal)

The reviewer's version is stronger than rev 2's and I adopt it: structure merely
freezes prior judgment, and both the old `propagation_terminal.py` and the
current `_ENVELOPE_FROM_ROW` show a clean control-flow shape encoding wrong
semantics; most claims are not machine-settleable; read-side discipline avoids
migrations across every producer and consumer; a detector over untyped prose is
likely noise.

**This case wins against a global schema/type campaign and against building the
incapacity detector now. I concede both.** It loses only against local runtime
guards where the evidence already exists and the bad terminal state is
self-sealing.

**Where I disagree with the reviewer.** Its point 2 argues that because 49 audit
detectors, `derivation_type`, evidence fields and capture-status contracts did
not prevent the motivating errors, "the missing resource is careful
interpretation, not always another field or detector." **That is a sampling
error.** All 49 detectors point at affirmative claims; zero point at negative
capability. Their failure to catch incapacity claims is not evidence about
structure — it is evidence that none of them was aimed at the class. Citing the
silence of instruments that were never pointed at the target is the same move as
concluding a service is down because you probed the wrong port. The reviewer
retains the asymmetry finding elsewhere, so I take this as an over-reach in the
steelman rather than a considered position.

**A second, narrower rebuttal.** The reviewer's point 4 offers read-side
skepticism as the cheaper substitute — "readers can treat negative terminal
states as claims, require the proof payload, reconcile periodically." For
**self-sealing terminal** states specifically, that is not merely weaker, it is
**inapplicable by construction**: `fail_row` transitions only `WHERE
status='open'` (`libs/charter_runner_store/propagation_ledger.py:207`), so the
row leaves `list_open_rows()` and the periodic reconciler never sees it again.
Read-side discipline works where the record stays in the queried set. Here the
defect removes itself from the reader's view. That is precisely why row 1 of the
partition needs a guard rather than a norm.

**On attack 4, conceded:** rev 2's "no amount of seat discipline could have
prevented those" was too absolute. Discipline could have operated at the author
or reviewer of `settle_open_row`, at the caller that initiated reconciliation, at
the reader of terminal failures, or in a periodic pass treating terminal
negatives as revisitable. The correct statement is: **runtime discipline was not
present at each execution, and author/reviewer discipline did not prevent the
encoded defect.** The reviewer also could not establish that no seat was in the
loop, and neither can I — the ledger does not record the settling caller.

**On attack 6 — it glances, and it does not land.** The charge is induction from
a narrow successful set (n=2). Two answers. First, the boundary condition was not
induced from the two exemplars; it is derived from the **falsifiability
asymmetry** — whether a bounded same-referent probe exists — and the exemplars
illustrate it. Second, the evidence is no longer two successes: §3 above adds
`cortex-api`'s false `code_version` and `stamp_fastapi_routes`'s unasserted `0`,
both **failure** cases, in **two further subsystems** (`openapi_mcp`,
`cortex_store` health), both squarely inside row 1. Induction from successes was
the charge; the answer is independent failures. The reviewer itself concludes
this attack "narrows the rhetoric but not the recommendation" — I agree the
rhetoric narrowed, and hold that the recommendation stands.

---

## The operator fork — read this before reconciling anything

**A queued operator decision reading "reconcile six false ledger rows" inherited
rev 2's error and should not be executed as written.** The framing came from me.
Ancestry establishes neither historical success nor present satisfaction, so
"false rows" is not a finding and no repair follows from it.

The live question is semantic, and **nothing in the data can settle it**:

> **Is a propagation ledger row an operation-history record, or an outstanding
> "at least this code is live" obligation?**

**Evidence for operation-history:**

- The implemented predicate is **exact equality** `code_version == code_ref`,
  which only makes sense as "this restart produced exactly this generation."
- Rows carry operation metadata: `mint_thread`, `mint_turn`, `reason`, `action`,
  `created_at`, `closed_at`, and a stored `proof_payload` — audit artefacts of a
  specific attempt.
- Terminality is coherent for a historical record: an operation that failed,
  failed.

**Evidence for outstanding obligation:**

- The surrounding machinery is a **work queue**: `list_open_rows`,
  `scoreboard_projection`, `defer_reason`, `bump_age_for_open_rows`,
  `safe_window`.
- `settle_open_rows_for_service` fires **after a drain completes**, to settle
  what is outstanding — queue semantics, not audit semantics.
- The operator's actual question is always "is my fix live?", never "did restart
  #4 succeed."
- **The decisive structural argument:** under exact equality, a row minted at
  commit X becomes **permanently unsatisfiable** the moment the service restarts
  to X+1. The proof can never again be met. So the operation-history reading
  makes the system *manufacture* terminal failures as a matter of routine for any
  row not settled before the next commit lands. That is incoherent for a token
  that means "this failed."

**My recommendation: the obligation reading, and the exact-equality predicate is
the thing that is wrong.** Under it: satisfaction becomes ancestry
(`is_ancestor(code_ref, observed_code_version)` — "at least this code is live");
an obligation is satisfied, outstanding, or **superseded** by a newer row for the
same service; and terminal failure becomes rare rather than routine. The seven
rows would then be re-opened and re-evaluated under ancestry, not "repaired."

**Held as a recommendation, not a conclusion, and I am not acting on it.** Two
things I cannot supply. Under the audit reading, the schema lacks the settling
caller, and the seven rows should be **annotated as unearned verdicts**, never
deleted. Under the obligation reading, ancestry cannot resolve a `code_ref` that
is not a commit at all — one failed row carries `code_ref = "working"`, and
`_is_literal_head` (`propagation_terminal.py:55`) special-cases only `"HEAD"`.

**`4aae67b2` stands regardless of how the fork resolves**, and that is a mark in
its favour: it governs only whether a terminal verdict may be written from a
probe of an unattributed generation, which is wrong under *both* readings. A
well-placed fix should not depend on an unsettled ontology.

---

## What I changed

**`4aae67b2`** — *fix(propagation): a non-answer must not terminally fail a ledger row*

- **New** `libs/charter_runner_store/propagation_determination.py` (115 lines).
  Separates *reading* a probe from *deciding* on it. `classify_probe` returns
  `matched | contradicted | indeterminate`; `observed_code_versions` handles flat
  and composite payloads and returns `None` when nothing readable came back;
  `outgoing_generation_ruled_out` requires **both** a restart boundary and a
  present `uptime_s`, so absence of either means *not ruled out*.
- **`propagation_terminal.py`** — a contradiction is terminal only when the
  incoming generation is positively ruled out; otherwise the row stays open with
  a naming defer reason. `reconcile_all_open_rows` can forward a boundary.
  Pre-existing `I001` cleared.
- **`test_propagation_terminal.py`** — **corrected accounting** (rev 2 said
  "three new, two updated," which double-counted the replacement). Verified from
  git: 10 tests before, 12 after. **Two added**
  (`test_guarded_mismatch_without_uptime_is_not_terminal`,
  `test_half_unreachable_composite_probe_is_indeterminate`); **one
  rewritten/replaced** (`test_queued_row_fails_on_mismatched_probe` →
  `test_unguarded_mismatch_leaves_row_open`); **one other updated**
  (`test_reconcile_before_after_counts`, expected failure count). The last two
  **pinned the behaviour I call a defect** — the specific judgment a reviewer
  should check.

A match remains sufficient on its own, deliberately: observed equals target is
self-validating whoever answered, while a non-match is not.

```
libs/charter_runner_store/test_propagation_terminal.py     12 passed, 2 warnings in 1.16s
libs/charter_runner_store/ + test_handler_propagation_disposition.py  26 passed, 2 warnings in 1.23s
tests/test_git_worker_drain_supervisor.py                   7 passed in 0.52s
ruff check (charter_runner_store, handler_propagation, drain supervisor)  All checks passed!
scripts/modularize scan (both changed modules)              Green (≤300): 2
```

No code changed in this revision — it is an argument correction. A directory-wide
`ruff check --fix` during rev 2 silently rewrote
`libs/charter_runner_store/db.py`, which my task did not touch; I confirmed the
change was mine (HEAD's `db.py` raises the matching `I001` under `--isolated
--select I`) rather than assuming, and reverted it.

---

## PROPAGATION REQUIRED

Every entry observed, not inferred from commit recency.

| Service | Restart at | Evidence |
|---|---|---|
| **model_manager / manage** (pid **765684**) | **`4aae67b2`** | **YES — and `4aae67b2` is itself landed-not-live.** Sole non-test importer of `propagation_terminal` is `git_worker_drain_supervisor.py:387` (ripgrep), which runs here. Started **11:24:04**; commit dated **14:07:03**. Process predates it; Python does not hot-reload. `/proc/765684/cwd` → this checkout. |
| **second `scripts.model_manager.ui`** (pid **669567**) | `4aae67b2` | Started **10:43:41** from a cursor-dispatch-home venv, cwd this checkout. Also predates. **Flagged, not diagnosed** — whether a second instance should run at all is an operator call. |
| **git_integration_worker** | — | **No restart for `4aae67b2`.** GIW does not import `propagation_terminal`; it imports `propagation_ledger` only (`handler_propagation.py:8-12`), unmodified. Running `82f07260`, observed via liveness. |
| **git_integration_worker** @ `4f7367ff` | — | **Already LIVE.** Independently re-verified: `merge-base --is-ancestor 4f7367ff 82f07260` → true; liveness reports `82f07260`. |
| **cortex-api** (pids **1173134** UDS, **1173844** TCP) | *not mine* | **Not a propagation request — a data point.** Both started 12:51:4x and report **different** `code_version`; the TCP value `dba38ed7` (committed 14:10:28) cannot be running. A separate worker owns the fix. Recorded here because the propagation contract consumes this field as proof. |

**No data-plane reconciliation is recommended** until §"The operator fork"
resolves. Rev 2 prescribed one; that prescription is withdrawn.

---

## Open questions and residuals

- **The fork above is the blocking question.** Settle by operator decision on row
  semantics, not by inspecting more rows.
- **Literal non-SHA `code_ref`s.** `code_ref = "working"` reaches the comparison
  and is reported as a version mismatch. `_is_literal_head`
  (`propagation_terminal.py:55`) covers only `"HEAD"`. My fix does not close
  this. Settle by widening the uncheckable-ref predicate to any `code_ref` that
  does not `rev-parse` to a commit, returning `unsettled`, never `failed`.
- **Who wrote the seven rows.** The payload shape is consistent with an unguarded
  sweep and inconsistent with post-restart verification, but the caller is not
  recorded, and there is currently **no production caller** of
  `reconcile_all_open_rows` — only the post-drain
  `settle_open_rows_for_service` (`git_worker_drain_supervisor.py:379-398`).
  Settle by recording the settling caller in the proof payload.
- **Does any consumer read `propagated` as proof-of-live?** Blocks the
  `submitted -> propagated` rename. Neither I nor the reviewer swept consumers.
- **Is `STATUS_COMPLETED` read as "new code is live"?** Settle by tracing every
  `RestartIntentStore` reader.
- **Would `incapacity_claim_unfalsified` catch the three motivating claims?**
  Unknown, and it is now the gate on building it. The three claims were never
  identified by ID in rev 1 or rev 2 — which is itself why the reviewer could not
  replay them. Identifying them is step one.
- **Four open `mcp`/`client_visible` rows** may now defer indefinitely rather
  than failing, under my change. That is the intended trade — open-and-visible
  beats terminally-wrong — but it needs a staleness signal on long-open rows,
  which `scoreboard_projection` does not provide.
