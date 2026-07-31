# B — The projection layer

Supersedes the pass committed at `4045b5e9`. Findings of that pass which survive
are carried forward and marked; the three places I disagree with it are stated
explicitly in **Disagreements with the prior pass**.

---

# Question

The brief asks: *should the closeout relay's re-projection exist at all?*

I sharpened it, and the sharpening is load-bearing:

> **Can a re-*derived* representation of executor judgment be trustworthy enough
> to serve as the bus-side record — and if it cannot, what does removing it cost
> the readers who depend on it?**

The prior pass argued the design question on principle: two representations of
one truth is a duplication smell, therefore delete one. That argument proves too
much — every cache and every summary is a second representation, and we keep
those. The answerable version is empirical: **measure what the projection
actually delivers to a reader**, then ask whether the delta is recoverable by
repair. So this pass is built on measurement of the real corpus (2,474 authored
sidecars in `tmp/reviews/closeouts/`), not on fixtures and not on principle.

---

# Verdict

**Delete the eleven-field prose re-projection — but not in the form or the order
the prior pass proposed.** The single strongest reason:

> **The layer's failure is dimensional, not parsing.** `RELAY_BODY_TARGET_CHARS`
> is 2,000 characters (`closeout_relay_briefing.py:33`) spread across eleven
> judgment fields. Measured over the real corpus, the relayed `ac_verdict` row
> has a **median length of 136 bytes** against a median authored sidecar of
> 4,274 bytes. A multi-criterion acceptance verdict does not fit in 136 bytes.
> This is arithmetic, not a bug, so no parser improvement reaches it — **I landed
> a parser improvement in this pass and the median stayed at 136 bytes.**

A representation that cannot fit its content is necessarily lossy. Because this
one is *derived* rather than *quoted*, its loss is **silent**: a truncated
quotation is visibly truncated, whereas a wrong projected cell is
indistinguishable from a right one.

The amendment the opposing case earns — a bounded **verbatim excerpt** alongside
the typed envelope, and a strict ordering behind sidecar durability — is in
**The case for keeping the layer**.

---

# What you found

Measurements below are from the shipped tree over the 220 most recent sidecars,
of which 85 classify as §2 and 67 carry `AC<n>` tokens in the authored prose.
The AC-token count is deliberately extractor-independent: it counts distinct
`AC<n>` tokens in the author's prose against those in the relayed row, so "loss"
is arithmetic rather than a judgment call.

```
§2 sidecars with AC tokens in authored prose : 67
  ac_verdict reported as an honest parse-miss:  0  ( 0%)
  ACs lost at EXTRACTION (unclamped cell)    : 36  (54%)
  ACs lost at DELIVERY  (relayed to the bus) : 51  (76%)
  relay body clamped to budget + pointer     : 38  (57%)

authored sidecar prose   median 4274B
relayed body             median 1573B
relayed ac_verdict row   median  136B   max 336B
```

### 1. The honesty fix at `4f7367ff` does not reach the field that needs it

`4f7367ff` is real and it is live (see **Propagation**). But `_extract_cell`
routes `ac_verdict` to `build_ac_verdict_cell`
(`closeout_relay_project.py:48-49`), and that function returns
`relay_parse_miss_cell` **only when the entire prose is empty**
(`closeout_relay_common.py:175-177`). Every other path returns an excerpt or a
`parse_failed` prefix.

Consequence, measured: **`ac_verdict` produced zero honest parse-misses across
all 67 sidecars, while silently dropping acceptance criteria in 51 of them.**

This is the sharpest form of the session thesis. The honesty machinery makes the
silent case *worse*, because a reader who sees that the relay flags what it
cannot find will reasonably infer that unflagged cells were found correctly.
`4f7367ff` fixed the case where the projector **knows** it failed; the residual
and larger case is the one where it **does not know**.

### 2. Root cause of the silent partial — localized

`_FIELD_HEADING_ALIASES["ac_verdict"]` contains `"ac1"`
(`closeout_relay_cortex_fields.py:9-15`), and `_heading_matches_field` matches by
**prefix** (`:52-54`). So any authored heading beginning `AC1 …` claims the whole
`ac_verdict` field. `_extract_atx_section` then returns the span up to the next
heading of any level (`:82`) — that is, AC1's subsection alone. AC2..ACn are
discarded with no signal.

The same hazard was already recognised **on the table path and fixed only
there**: `_table_heading_matches_field` explicitly rejects `ac\d+` rows, with the
docstring *"Strict table-row field match — reject AC1-style verdict rows"*
(`:59-72`). One hazard, two surfaces, one patched. That asymmetry is itself an
argument about this layer: its defects are not converging, they are being
narrowed one surface at a time.

### 3. Compliance is not the variable — the decisive specimen

`section2_fields.py:30-38` generates the instruction telling executors to use the
canonical headings verbatim. **59 of 85 sidecars comply.** It does not save them.

`auto-a4e28248c3bb.md` authored, at line 112, the exact canonical heading with a
complete five-row verdict table:

```
### ac_verdict

| AC | Verdict |
|---|---|
| AC1 | **PASS** — intent `completed`, updated_at=… |
| AC2 | **PASS (negative)** — row still `open`, defer_reason=… |
…five rows…
```

The relay delivered, as that executor's `ac_verdict`, a JSON blob about
`intent 18e42d50` taken from a `**AC1 — intent …**` subsection **one hundred
lines earlier** — with no parse-miss flag, under a `status: complete` header.
`extract_field_section` tried the bold extractor before the ATX extractor
(`:204-216`), so an earlier fuzzy match beat a later exact one.

This kills the cheapest available remedy. The failure is not executors authoring
badly; it is the projector preferring the wrong section of correctly-authored
prose.

### 4. The clamp destroys what extraction recovers

Extraction loses ACs in 36 of 67 sidecars; **delivery loses them in 51**. The
15-sidecar gap is the clamp's own contribution — `clamp_relay_body` cell-budgets
and slices every body above 2,000 chars (`closeout_relay_briefing.py:157-206`).

Concretely: my fix raised `auto-a4e28248c3bb.md`'s extracted `ac_verdict` from
915 bytes of wrong content to 4,393 bytes of correct content. The reader still
receives **264 bytes**, because the clamp cuts it back. The repair is real and
the reader cannot tell.

### 5. The projection does not deliver the small turn it exists for

The brief names the layer's purpose: a bus turn should be small and a reader
should not need a second fetch. Measured against that purpose:

| representation | median size | compression |
|---|---|---|
| authored sidecar | 4,274 B | — |
| current projection | 1,573 B | 2.9× |
| typed envelope | 234 B | 18.3× |

And **57% of bodies already exceed the budget, get clamped, and have
`Full closeout: <pointer>` appended** (`closeout_relay_briefing.py:196-199`).
For the majority of real closeouts the reader **already** pays the second fetch
*and* receives a mangled table. The small-turn defence does not merely fail to
justify the layer — it argues against it, because the envelope is 6× smaller
than the projection.

### 6. `files_modified` is not in the projection at all

`SECTION2_FIELDS` (`section2_fields.py:10-22`) has eleven entries and
`files_modified` is not among them. It originates in the machine capture manifest
(`cursor_sdk_closeout.py:422`, `:899`). This settles the a:27334 question below.

---

# Does a:27334 share the projection's root cause?

**Half of it does, and the half that does is now localized. The other half does
not.** The brief's *"same module, probably same root"* is not correct, and the
distinction matters because it splits one ticket into two fixes with different
owners.

| symptom | root cause | shares projection root? |
|---|---|---|
| relayed table shows only the first AC | `ac1` alias + prefix match + first-match-wins (`closeout_relay_cortex_fields.py:9-15`, `:52-54`, `:82`), then the 2,000-char clamp | **yes** |
| `files_modified` empty while sidecar is complete | machine capture path; `capture_status` may be `partial`/`unavailable` while `work_outcome=shipped` | **no** — different subsystem, not a projected field |

They co-occur, and that is why they were filed as one defect: both are
**unflagged absences**, the same epistemic error in two subsystems. But fixing
the projector does not populate `files_modified`, and repairing capture does not
stop the projector truncating a verdict.

**Effect on the deletion case: it strengthens it.** Had a:27334 been wholly the
projector's fault, it would be a bug report against a repairable component. That
one visible symptom decomposes into an unfixable dimensional limit *plus* an
unrelated capture-status gap shows the projection is not a component with bugs
but a layer that manufactures a *class* of them — and that the class is the one
the session thesis names.

---

# The case for keeping the layer

The brief's own framing — *a bus turn should be small and a reader should not
need a second fetch* — is the weak form, and §5 above defeats it on its own
numbers. Here is the strong form, which survives that.

> **The projection is the only durable, self-contained record of executor
> judgment that reaches a reader, and some readers cannot fetch anything else.**
>
> 1. **`source_ref` points into ephemeral storage.** It resolves to
>    `workspaces://tmp/reviews/closeouts/<id>.md`. `tmp/` is gitignored at
>    `.gitignore:47`, and `git ls-files tmp/reviews/closeouts/` returns
>    **0 of 2,474 files**. The sidecar is untracked single-host working-tree
>    state: one `git clean -fdX`, one fresh clone, one disk failure and every
>    authored closeout is gone. The bus turn, by contrast, is durable. Deleting
>    the projection therefore moves the canonical record *from* a durable store
>    *to* an ephemeral one — the wrong direction, and irreversibly so.
> 2. **Some callers structurally cannot dereference the pointer.** This codebase
>    already models exactly that: `caller_auditable.py:26-31` is a deny-by-default
>    allowlist of requesters who can re-observe a deliverable, and the blind life
>    seat is explicitly outside it. For a blind caller the relay body is not a
>    convenience, it is the entire artifact. Deleting it does not make them fetch
>    the source; it makes them read nothing.
> 3. **The module already acts on that distinction**, clamping status when a
>    non-auditable caller is missing reporting fields
>    (`closeout_relay_reporting.py:99-163`). A replacement design that assumes
>    every reader can dereference `workspaces://` would contradict a capability
>    distinction this very module draws and enforces.

That is a serious argument and it is not the one the brief anticipated.

## Answering it

It is **right about the requirement and wrong about the artifact.** It
establishes that the bus needs a durable, self-contained record for readers who
cannot dereference. It does not establish that the record should be *re-derived
from prose by a fuzzy heading matcher*.

**(a) The projection does not satisfy the requirement it is being defended by.**
A durable record must be faithful, and this one is not: 76% of relayed verdicts
drop acceptance criteria, the median row is 136 bytes, zero misses are flagged,
and 57% are clamped with a pointer appended anyway. Worse, the argument's
strongest constituency is the one most harmed — **the blind caller is precisely
the reader who cannot detect the substitution.** For them the relay does not
preserve judgment; it replaces it with a plausible-looking 136-byte artifact
labelled `complete`. A record that silently substitutes content is worse than a
dangling pointer, because a dangling pointer announces itself and a wrong cell
does not.

**(b) If durability is the requirement, then durability is the fix — not
re-derivation.** Preserve *bytes the executor wrote*: a bounded **verbatim
excerpt**, explicitly labelled as an excerpt, carried in the envelope. This costs
the same bus bytes and no parser at all. And it is honest by construction, which
is the entire point: a quoted excerpt that is truncated is *visibly* truncated,
whereas a projected cell that is wrong looks exactly like one that is right.
**The distinction between a quoted excerpt and a derived cell is precisely the
claim-versus-observation distinction this whole session is about.**

**(c) The ephemerality is a separately-filed defect, not a licence.** `tmp/`
gitignoring is a:27411 / Workstream E, with its own open remediation question.
Justifying a lossy projection by an unfixed storage defect fixes the wrong layer,
and it quietly promotes the projection to permanent infrastructure for a
condition that is supposed to be temporary.

## What the steelman wins

It does not overturn the verdict, but it changes it in two ways, and both are
corrections to the prior pass:

1. **Envelope-only is wrong.** The prior pass proposed status + basis +
   `closeout_source` + `source_ref` + capture status + bounded effects. That
   leaves the blind caller with nothing but a pointer they cannot follow. The
   replacement must additionally carry a **bounded verbatim excerpt of the
   authored §2**, labelled as an excerpt. This is the concession the opposing
   case earns, and it preserves the no-second-fetch property *honestly*.
2. **Deletion must be ordered, and durability comes first.** The prior pass
   listed only Workstream A's response schema as a blocker. There is a strictly
   prior one: today the sidecar is not durable and not universally
   dereferenceable. Deleting the projection before that is fixed would trade a
   lossy record for no record.

**Sequence:** (i) give the closeout a durable, dereferenceable home — Workstream
E's question answered first; (ii) replace the eleven prose fields with the typed
envelope **plus a bounded verbatim excerpt**; (iii) only then delete the
projector.

If step (i) proves impossible — if there is genuinely nowhere durable to put a
closeout — then the opposing case wins outright and the layer must stay, in which
case the honest thing is to cap it at a verbatim excerpt anyway and stop deriving
cells. **Either branch ends with derivation removed.** That is the finding.

---

# What you changed

**`55e69868`** — `fix(relay): exact §2 heading outranks prefix-matched AC1 subsection`
(`closeout_relay_cortex_fields.py`, `test_cursor_auto_closeout_relay.py`).

The heading extractors now run twice: an exact-field-heading pass first, then the
pre-existing prefix pass. The loose pass is untouched, so **the set of fields
that resolve is identical** — only *which* section wins can change, and it
changes toward the heading the author actually named.

That property was not a guess. My first attempt was the obvious fix — delete the
`"ac1"` alias — and observation killed it: `auto-379d67bb7092.md` went from a
wrong `ac_verdict` to `source=empty`, **the entire closeout dropped from the
bus**. `looks_section2` (`closeout_relay_common.py:105-121`) gates §2 detection
on `ac_verdict` resolving, so making extraction stricter silently made detection
stricter too. I reverted it. Both behaviours are now pinned by regression tests
(`test_canonical_ac_verdict_heading_beats_earlier_ac1_subsection`,
`test_ac1_subsection_still_resolves_when_no_canonical_heading`).

Also repaired a test premise stale since `4b056a34` restored `web-anthropic` to
the auditable allowlist; the genuinely blind seat is `mcp-claude-life`, so the
test now asserts its subject rather than an outdated fact.

**Verification, quoted:**

- `ruff check` on `closeout_relay_cortex_fields.py` — `All checks passed!`
- `compileall` — clean.
- `pytest services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py -q`
  → **`53 passed in 0.36s`**
- Full GIW suite (`--ignore=test_cursor_sdk_gate.py`, which fails collection at
  HEAD): **`36 failed, 934 passed, 1 skipped`** against a pristine-HEAD baseline
  of **`37 failed, 931 passed, 1 skipped`**, measured by swapping my two files
  for their HEAD versions and re-running. One fewer failure, three more passes,
  no new breakage. The remaining 36 are pre-existing and outside this module.

**Honest assessment of the value of this commit:** it is small. It corrects the
cell for compliant authors and removes one silent-corruption surface, but the
clamp still truncates the result, so most readers will see no difference. I
landed it because deletion is ordered behind two other workstreams and the layer
will keep running in the meantime — not because it repairs the layer. **It is
also the evidence for the verdict:** this is what a competent, safe, tested fix
to this layer buys, and it is a median of 136 bytes.

---

# What you did NOT change and why

- **The projector itself.** Deletion is ordered behind sidecar durability and
  Workstream A's response contract, and it has callers outside my six files
  (`nested_outcome.py:172-278` — carried forward from the prior pass, verified).
- **The `ac1` alias.** Removing it is the obvious fix and it is wrong; observed
  `source=empty` regression above.
- **`looks_section2`'s dependence on `ac_verdict` resolving.** This coupling is
  what made the obvious fix dangerous — a detector keyed on one fuzzy field
  rather than on field *count*. Fixing it means changing relay routing for every
  closeout, which is a wide change to a layer recommended for deletion. Residual.
- **A completeness or confidence signal on cells.** This is the principled repair
  — a cell should be able to say "extracted, possibly partial". I did not build
  it, because it is new mechanism inside a layer I am recommending be deleted,
  and because the clamp would truncate the signal along with the content.
- **The capture path (`files_modified`).** Different subsystem, different root
  cause, and largely outside my territory.
- **`_effects.py`, `_reporting.py`, `_briefing.py`, `_common.py`, `closeout_relay.py`.**
  Read for evidence; nothing changed. Their compensating clamps are, as the prior
  pass correctly observed, evidence that the projector became a second semantic
  authority — that finding survives and I reaffirm it.
- **Pre-existing defects left to their owners:** a `ruff` I001 import-order error
  at `test_cursor_auto_closeout_relay.py:1522` (present at HEAD, in a test I did
  not author); the `test_cursor_sdk_gate.py` collection error; the 36 suite
  failures. Fixing them would have swept unattributed changes into my diff.

---

# PROPAGATION REQUIRED

Observed, not inferred. GIW self-reports its running SHA:

```
$ curl -sf http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness
{"live":true,"lane":"cursor-auto",…,"uptime_s":4871.445,
 "code_version":"82f07260570c2d9531752bd2a404c55b8d2f24da"}

$ ps -o pid,lstart -p 1173131
1173131  Fri Jul 31 12:51:44 2026
```

| commit | ancestor of running `82f07260`? | state | action |
|---|---|---|---|
| `55e69868` (this pass) | **no** | **landed-not-live** | restart `git_integration_worker` |
| `4f7367ff` (prior relay fix) | **yes** | **LIVE** | none — see below |

Settled by `git merge-base --is-ancestor <sha> 82f07260`.

**`git_integration_worker` is the only service requiring a restart for this
workstream.** No other service imports the relay modules — verified by searching
for importers of `closeout_relay_cortex_fields` / `extract_field_section` /
`field_heading_present`, which returns only the eight `closeout_relay*` modules
and their test file.

### Later in the session: GIW went down, and the restart became a start

Re-probing before closing out, the service is **no longer running**:

```
$ curl -s -w "http_code=%{http_code}" .../cursor-auto/liveness   ->  http_code=000
$ ss -ltn | grep 8091                                            ->  nothing listening
$ ps -p 1173131                                                  ->  gone
```

Confirmed twice, 45 s apart. It was live at `82f07260` with 4,871 s uptime
earlier in this same session, so it stopped in between. I did not stop it and
cannot start it (no `manage`); a sibling workstream owns the drain supervisor and
this may be a drain in progress.

**What this changes for the seat that can run `manage`:** the action is now a
**start**, not a restart, and `55e69868` is already an ancestor of `HEAD`
(`5a259cee` at time of writing) — so a start from `HEAD` should bring it live
without any separate step for this workstream.

**Do not treat that as done.** It is exactly the inference this brief exists to
forbid. Start the service, then read `code_version` back from the liveness
endpoint and run `git merge-base --is-ancestor 55e69868 <observed_sha>`. An
unobserved propagation claim is the failure class, and "it should pick it up from
HEAD" is an unobserved propagation claim.

**On `4f7367ff`:** the prior pass reported it landed-not-live and then withdrew
that on lead verification. The withdrawal was correct and I confirm it
independently above. I have removed the struck-through text rather than carry it,
but the lesson it recorded is retained and generalised: **a propagation entry
must cite an observed `code_version`, never an inferred one.** That failure —
asserting a propagation state when the service will report it on request — is the
same error this workstream studies, committed by the seat studying it.

---

# Disagreements with the prior pass

1. **On whether the first-AC-only symptom is reproducible.** The prior pass wrote
   that its fixtures *"do not reproduce a current first-AC-only loss after
   `4f7367ff`"* and that *"a real failing dispatch ID and its sidecar/wrapper
   pair are required to localize any remaining failure."* **This is wrong, and
   the way it is wrong is the session thesis.** It is reproducible at scale —
   36 of 67 sidecars at extraction, 51 at delivery — and I localized it to
   `closeout_relay_cortex_fields.py:9-15` + `:52-54` + `:82` without any dispatch
   ID. The prior pass looked at *test fixtures*, found the evidence absent, and
   filed a requirement for evidence that was already sitting in the same
   repository in 2,474 files. **A claim of insufficient evidence is a claim**,
   and it was falsified by one script against the corpus.
2. **On envelope-only as the replacement.** Its six-field typed envelope leaves
   blind callers with an unfollowable pointer. The replacement needs a bounded
   verbatim excerpt. The prior pass raised this as an open question and then
   proposed a design that does not answer it.
3. **On changing nothing.** It framed any production change as *"another parser
   patch"* and declined. But its own verdict orders deletion behind other
   workstreams, so the layer keeps running — and a live silent-corruption surface
   in a layer that will keep running for weeks should be narrowed. Declining to
   act is only free if deletion is imminent, and by its own analysis it is not.

**Carried forward and reaffirmed:** the sidecar is the richest authored artifact
and is given priority over the SDK wrapper (`closeout_relay.py:222-236`); the
compensating clamps in `relay_trust.py:33-37`, `_effects.py:406-429` and
`_reporting.py:99-163` are evidence the projector became a second semantic
authority; empty `files_modified` must never be read as evidence of no edits;
adding extractor heuristics is the wrong direction.

---

# Open questions and residuals

| # | Question | What would settle it |
|---|---|---|
| R1 | Where does a closeout live durably? `tmp/` is gitignored and 0 of 2,474 sidecars are tracked. **This blocks deletion and is the highest-value open item in this workstream.** | Workstream E's question — is `**/test_*.py` / `tmp/` exclusion deliberate? — answered, then a durable home chosen (tracked path, cortex share, or artifact store) |
| R2 | Can every relay consumer dereference `workspaces://`? `caller_auditable.py:26-31` says some structurally cannot. | Workstream A's endpoint + client inventory. If any cannot, the excerpt in the envelope is mandatory rather than optional |
| R3 | Should `looks_section2` key on field *count* (≥2 of eleven) rather than on `ac_verdict` resolving? The current coupling turned an extractor fix into a detection regression. | Replay all 2,474 sidecars through both detectors and diff the classification; the change is only safe if the diff is empty or strictly more permissive |
| R4 | What is the `files_modified` capture gap — the other half of a:27334? | A dispatch where the sidecar names changed files and the manifest is empty, with its `capture_status`. Different owner from this workstream |
| R5 | Is 2,000 chars the right relay budget, and is it a transport limit or a convention? | If it is a convention rather than a hard bus limit, a verbatim excerpt could be larger and the whole calculus shifts. Nobody has stated which it is |
| R6 | Should cells be able to report "extracted, possibly partial"? | Only worth building if R1 concludes the layer must stay |

**If you read one thing from this workstream:** the projector reports zero parse
misses on `ac_verdict` across 67 real closeouts while silently dropping
acceptance criteria in 51 of them. It is never uncertain and it is usually wrong.
