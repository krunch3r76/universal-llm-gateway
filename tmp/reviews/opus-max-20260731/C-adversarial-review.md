# Workstream C — adversarial review

## Verdict

**C survives, but only after narrowing two claims and demoting one recommendation.**

The durable part is conditional, not global:

> When a machine-emitted claim has a bounded probe of the same referent, the
> runtime path should require the probe result and preserve “unknown” rather
> than relying on a producer or reader to remember a rule.

Discipline does **not** beat that rule for repeatable runtime state transitions.
It does beat structural expansion outside that boundary: judgments,
constitutive policy, observer-scoped output, and untyped prose claims.

The strongest attack that lands is against C's “decisive empirical evidence.”
The six ancestor refs do **not** demonstrate that six historical propagation
operations succeeded. A descendant running now proves that the target code is
included now; it does not prove that a particular earlier restart succeeded at
the time represented by its ledger row. The database proves something narrower
and still important: seven terminal failures were written from rapid probes of
the same old generation without evidence that the incoming generation had been
observed. Those verdicts were **unearned**, but the six are not independently
proved historically false.

C should retain the structural recommendation, rewrite the empirical claim,
limit its rejection to **self-reported** provenance fields, and make the
incapacity detector prototype-contingent rather than endorsed now.

## Attacks attempted and their outcomes

Ranked by whether they change the recommendation.

### 1. *lands* — the six ancestors do not prove six successful propagations

C says the ledger records failure for “propagations that demonstrably
succeeded” because six `code_ref`s are ancestors of the running
`82f07260570c...`.

That inference changes referents:

- The row's explicit proof obligation is exact:
  `code_version == code_ref`
  (`libs/implement_admission/propagation_row.py:56-63`).
- The current observation answers: “does the currently running descendant
  contain this commit?”
- The historical row purports to answer: “did this propagation/restart produce
  the required live generation at that time?”

A later successful restart to a descendant can make an ancestor live even if an
earlier restart attempt failed. Ancestor reachability therefore cannot establish
the historical operation's success.

What the rows **do** prove is enough to indict the old terminalization:

- all seven were marked failed within 0.083 seconds;
- their payloads observed the same `3e6ca550...` generation;
- uptime increased monotonically from 929.565 to 929.649 seconds;
- terminal failure was therefore assigned before an incoming generation was
  positively identified.

The correction in `4aae67b2` is still directionally justified: a probe of an
unattributed generation cannot support an irreversible failure. But C must say
“terminal failures unsupported by their proof,” not “six propagations
demonstrably succeeded.”

**Recommendation effect:** keep the control-flow fix; weaken the evidentiary
claim and do not prescribe one-off row repair merely from present ancestor
reachability. Reconciliation needs an explicit semantic decision: is a row an
operation-history record, or an outstanding “at least this code is live”
obligation?

### 2. *lands* — C rejects too broad a family under “provenance-as-data”

C defeats a bare producer-filled enum:

```text
value = "executed"
basis = "observed"
```

That is indeed two self-reported claims. It does **not** defeat a
framework-issued observation value whose constructor is reached only through
the probe path, for example an `Observed[T]` returned by the probe adapter and
required by the terminal transition. That design is type shape and control flow,
not “discipline with an extra field.”

The new OpenAPI substrate demonstrates part of the mechanism:

- route identity is stamped at the decorator
  (`libs/openapi_mcp/binding.py:40-56`);
- extraction rejects malformed and duplicate bindings
  (`binding.py:59-103`);
- typed route metadata is derived from the served document rather than a
  second route table.

It does **not** currently enforce response provenance. A route author still
declares `x-mcp`, and OpenAPI cannot establish that a probe ran. So Workstream A
does not invalidate `4aae67b2`, and C is right that C does not depend on A.

But the dichotomy “control flow or provenance field” is false. A branded
observation token, minted by the probe framework and exposed in a response
schema, is both. C should reject **self-attested provenance enums**, not
schema-visible provenance categorically, and should say that A could carry a
framework-enforced observation type later.

**Recommendation effect:** expand the admissible structural options. Prefer
direct derivation locally; use framework-issued typed evidence at subsystem
boundaries where direct control-flow coupling is undesirable.

### 3. *lands* — `_disposition_for` makes one contradiction impossible, not an evidence-free claim

C presents `_disposition_for` as making an outer value impossible without its
evidence. The actual map contains:

```text
"submitted" -> "propagated"
```

at `services/git_integration_worker/cursor_auto/handler_propagation.py:155-160`.
The summary then explains that the row is still open
(`handler_propagation.py:233-235`).

Thus the function makes **`propagated` over a failed execution row**
unrepresentable. It does not make **a propagated-looking token without
proof-of-live** unrepresentable. C notices this later as a scope leak, but that
concession contradicts the breadth of the exemplar in the verdict.

This is not merely wording. Structure can faithfully enforce a semantically bad
mapping. The same problem appeared in the original
`propagation_terminal.py`: the control-flow shape was good while the leaf
predicate hardened a non-answer into failure.

**Recommendation effect:** replace “remove the code path by which a value can be
produced without its evidence” with the narrower, testable invariant for each
site. For this envelope: “outer status is a total function of row statuses, and
unproved submission has a token that cannot be read as live completion.”

### 4. *lands*, but does not overturn — discipline could reach the machine path

“No amount of seat discipline could have prevented those” is too absolute.
Discipline could operate at:

- the author/reviewer of `settle_open_row`;
- the caller that initiated reconciliation;
- the reader of terminal ledger failures;
- a periodic reconciliation that treats terminal negatives as revisitable.

The database does not record the settling caller, and the current tree has no
production caller of `reconcile_all_open_rows`; only the post-drain
`settle_open_rows_for_service` call remains
(`scripts/model_manager/ui/controller/git_worker_drain_supervisor.py:379-398`).
I therefore could not establish “no seat was in the loop” as a historical fact.

This attack does not make discipline preferable at runtime. A norm applied at
code review is once-per-change; a structural transition guard is applied on
every execution. The right conclusion is “discipline alone did not reliably
protect runtime terminalization,” not “discipline cannot reach code.”

### 5. *lands* — the incapacity detector is not ready to endorse

C's proposed detector depends on a negative-capability predicate class that does
not exist. Current assertions have:

- `derivation_type`, including `direct_observation`, `agent_observation`, and
  `inference` (`libs/cortex_store/models/assertions.py:18-29`);
- `observed_at`, `evidence`, and `evidence_uris`;
- optional `predicate_form` and free-form `attributes`
  (`models/assertions.py:35-97`).

Those fields show that schema-level provenance already exists in part, but it is
caller-supplied and does not classify negative capability. The detector cannot
be usefully evaluated until either producers type these claims or a classifier
does so with measured precision.

The registry also contains a warning against premature optimism:
`implement_ready_spec_unvalidated` is disabled after false-positive behavior
(`libs/cortex_store/dispatch_ops/ops_audit_detectors.py:238`).

C itself admits it has not replayed the three target claims. That means “one
advisory detector” is a hypothesis, not a recommendation. The honest gate is:

1. obtain the three source claims;
2. prototype classification and evidence-shape checks;
3. measure catches and false positives;
4. add the detector only if it catches the motivating cases without attacking
   policy denials or observer-scoped output.

**Recommendation effect:** retain the asymmetry finding; demote the detector
from endorsed to experiment-gated.

### 6. *glances* — the structural principle is induced from a narrow successful set

The two exemplars do not generalize across envelope surfaces:

- projections combine machine capture, authored prose, and fallback parsing;
- judgments have no bounded probe;
- observer-scoped negatives require consumer interpretation;
- wire renames have consumer migration cost.

C already limits mandatory observation to a bounded, referent-matched probe and
leaves three classes as assertions. Once that limit is taken seriously, the
principle is no longer a global type-system proposal; it is a targeted runtime
invariant. The n=2 criticism therefore narrows the rhetoric but not the
recommendation.

### 7. *fails* — “basis: observed” does not solve scope

I tried to rescue schema provenance for scoped observer output. C is right on
this point. Marking `files_modified=[]` as observed says how the list was
produced; it does not say whether the observer had complete coverage.

The current capture code already documents the actual contract:
`effects` is authoritative only when `capture_status=complete`
(`services/git_integration_worker/cursor_sdk_capture_status.py:396-411`).
The relay likewise warns that an empty machine capture is not authority
(`cursor_auto/closeout_relay.py:181-185`).

The repair is a scope-bearing sum type or naming such as
`files_observed_modified`, plus an unrepresentable empty-authoritative state
when coverage is degraded. A provenance enum alone would make the misreading
look better founded.

### 8. *fails* — discipline beats structure for referent-matched runtime facts

The strongest discipline case still loses for a narrow class:

- the producer already has a bounded probe;
- a terminal transition is irreversible or self-sealing;
- unknown can be represented without operational harm;
- the structural change is local.

Here, asking every future author and reader to remember the rule is strictly
weaker than making terminalization require a positive determination. The
discipline case wins elsewhere, but not at this boundary.

## The strongest case for discipline-over-structure

The strongest version is not “write another rule.” It is:

1. **Structure merely freezes prior judgment.** Both the old
   `propagation_terminal.py` and current `_ENVELOPE_FROM_ROW` show that a clean
   control-flow shape can encode the wrong semantics. More structure can make a
   mistaken ontology harder to detect and migrate.
2. **The fleet already has structured provenance.** Assertion
   `derivation_type`, evidence fields, predicate forms, 49 audit detectors, and
   capture-status contracts did not prevent the motivating errors. This is
   evidence that the missing resource is careful interpretation, not always
   another field or detector.
3. **Most claims are not machine-settleable.** Capability, design, policy,
   causal attribution, and observer scope cannot be reduced to a same-referent
   probe without false rigidity.
4. **Read-side discipline is cheaper and more adaptable.** Readers can treat
   negative terminal states as claims, require the proof payload, reconcile
   periodically, and expire incapacity claims. That avoids migrations across
   every producer and consumer.
5. **A detector over untyped prose is likely noise.** Typing the population
   first is an ontology migration; doing so for three incidents may cost more
   than a strong norm plus targeted reconciliation.

This case **wins against a global schema/type campaign and against building the
incapacity detector now**. It loses against local runtime guards where evidence
already exists and a bad terminal state is self-sealing. C's final boundary is
therefore substantially right, but “structural” should be presented as a
targeted enforcement placement, not a general answer to claim provenance.

## Factual claims I verified independently

### Ledger rows and running version

I queried the SQLite database read-only with:

```text
"$HOME/.venvs/universal/bin/python" -c '<sqlite mode=ro query:
SELECT row_id, service, code_ref, status, defer_reason, proof_payload, closed_at
FROM propagation_ledger
WHERE service="git_integration_worker" AND status="failed">'
```

Observed:

- seven failed rows, all `defer_reason=code_version_mismatch`;
- six resolvable commit refs and one literal `working`;
- all seven payloads observed `3e6ca550a1ac...`;
- uptime 929.565 through 929.649 seconds;
- closure span 0.0831377506 seconds.

I fetched:

```text
curl -fsS http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness
```

It returned `live=true`, `code_version=82f07260570c...`, and
`uptime_s=4814.728`.

I resolved each ref with `git rev-parse --verify <ref>^{commit}` and checked
`git merge-base --is-ancestor <resolved> 82f07260570c...`:

- all six commit refs are ancestors;
- `working` does not resolve.

`fail_row` and `close_row` both update only `WHERE status='open'`
(`libs/charter_runner_store/propagation_ledger.py:185-239`), and normal listing
returns only open rows (`propagation_ledger.py:104-136`). The seven terminal
rows are not revisited by the current settlement loop.

**Correction to C:** the payloads are not one shared probe. Differing
`handler.age_s`, `uptime_s`, and closure timestamps show seven sequential probes
in one 83 ms sweep. The same-generation conclusion holds; “single probe” does
not.

### Detector count and direction

I counted the keys in `get_all_detectors()` by AST and also imported the module:

```text
registry=49, all_kinds=49, graph=37, fs=10, info=3
```

I repeated the AST count against
`git show 4d1bbdcf:libs/cortex_store/dispatch_ops/ops_audit_detectors.py`; it was
also **49** at C's own documentation commit.

Filtering detector keys for
`incapac|unavail|cannot|unsupported|capab|denial|impossible` returned zero.

**Correction to C:** the asymmetry holds, but “48 detectors” did not. The
verified count is **49 versus zero**.

### `4aae67b2` and its tests

`git show --name-status 4aae67b2` confirms exactly three paths:

- added `propagation_determination.py`;
- modified `propagation_terminal.py`;
- modified `test_propagation_terminal.py`.

Comparing test function names before and after showed:

- old file: 10 tests;
- new file: 12 tests;
- `test_queued_row_fails_on_mismatched_probe` was replaced by
  `test_unguarded_mismatch_leaves_row_open`;
- two additional tests were added:
  `test_guarded_mismatch_without_uptime_is_not_terminal` and
  `test_half_unreachable_composite_probe_is_indeterminate`;
- `test_reconcile_before_after_counts` changed its expected failure count.

I ran:

```text
"$HOME/.venvs/universal/bin/python" -m pytest \
  libs/charter_runner_store/test_propagation_terminal.py -q
```

Observed: `12 passed, 2 warnings in 1.32s`.

**Correction to C:** “three new regression tests; two existing tests updated” is
miscounted. Strictly, it added two tests, replaced/rewrote one existing test,
and changed one other existing test.

### OpenAPI enforcement

The landed `libs/openapi_mcp/` code structurally derives MCP route bindings and
validates their shape. It carries no response-provenance type and cannot by
itself prove a probe executed. C's “composes, does not depend” conclusion holds
for the code currently present.

## What I could not check

- The three motivating false incapacity assertions were not identified by ID or
  source path in C, so I could not replay a proposed detector against them.
- The ledger does not record the settling caller. I could not prove who invoked
  the historical sweep or whether a seat initiated it.
- I do not have a historical liveness observation from the moment each row's
  restart should have completed. Present ancestor reachability cannot supply
  that missing fact.
- I did not test service restarts or MCP surfaces; the mission explicitly marks
  MCP tools unavailable, and this review was read-only.
- I did not prove whether downstream consumers interpret `propagated` as
  proof-of-live; C also left that consumer sweep open.

## What C should change

1. Replace “six propagations demonstrably succeeded” with “seven terminal
   failures lacked evidence that the incoming generation had been observed.”
   Keep ancestor reachability as evidence that their obligations may now be
   satisfiable, not as proof of historical operation success.
2. Correct “single probe” to “seven rapid probes of the same generation in one
   sweep.”
3. Correct `48` to `49`; retain “zero negative-capability detector names.”
4. Correct the test accounting to two added, one rewritten/replaced, and one
   other updated.
5. Narrow “provenance-as-data is discipline with extra steps” to
   **self-reported provenance enums**. Admit framework-issued typed evidence as
   a structural option, especially across subsystem boundaries.
6. Narrow the `_disposition_for` claim: it prevents contradiction with failed
   rows, but `submitted -> propagated` leaves an evidence/scope leak.
7. Replace “no amount of seat discipline could have prevented” with “runtime
   discipline was not present at each execution; author/reviewer discipline did
   not prevent the encoded defect.”
8. Demote `incapacity_claim_unfalsified` to a prototype gate until the three
   motivating claims are replayed and classification precision is measured.
9. State the final recommendation as a partition:
   - local, bounded, same-referent machine facts → structural runtime guard;
   - cross-boundary observed values → direct derivation or framework-issued
     typed evidence;
   - policy, judgment, observer scope, and untyped prose → discipline, explicit
     naming, expiry, and read-side skepticism.
