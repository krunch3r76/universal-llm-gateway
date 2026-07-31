# Opus Max window 2 — 2026-07-31

Brief: `cortex://notes/system/specs/opus-max-window2-brief-20260731.md`.
Predecessor: `tmp/reviews/opus-max-20260731/SUMMARY.md` (window 1, at `369ca56b`).
Window-2 span: `369ca56b` → `a84f1977`, eight commits. Working tree clean.

This window ran out of usage budget shortly after starting, resumed in a
reduced-budget mode, and deliberately narrowed from six parallel targets to one
finished one. What follows separates the finished thing from the surveys.

---

## 1. Propagation

| Service | Restart at | Why | Verified live? |
|---|---|---|---|
| `cortex_api` | `a84f1977` | All write-op repairs are in `libs/cortex_store`; the running process still serves the old behaviour | **No** — landed, not live |

Nothing else needs a restart. The two `stargate` commits (`d5e7b609`, `733ab0a7`)
track test files that were invisible to git; they change no runtime code.

**Landed ≠ live, and nothing in this window was verified live.** No agent here
restarted anything. The human operator runs it.

Carried from window 1 and still true: the fleet's surfaces **deliberately report
different SHAs from each other**, each accurate to what that process actually
loaded. That is the fix, not a defect. Do not reconcile them.

---

## 2. What is finished

**The three `cortex_store` write-op defects — the tax every seat pays on every
graph write.** This was the brief's §3.1 and the one target taken to completion.

| Defect | Verdict | Commit |
|---|---|---|
| `assertion_update` rejected `evidence_uris` and `reasoning_summary`, so auditor evidence could not reach the structured slot the auditor gate reads | **Fixed** — threaded through dispatch op, model, route, shared layer, with a test | `aebb09a5` |
| `friction` demanded `note` and rejected `claim`, and reported missing fields one at a time | **Fixed** — batched requirements landed; the `claim` alias turned out to already work | `ad14a40a` |
| `supersede` demanded `old_assertion_id`, then on retry `evidence` — two round trips to learn one call's requirements | **Fixed** — now returns all five missing fields in one response | `8a00b2d3` |

Eight tests across two files, all green, all tracked, each demonstrated to go red
when the behaviour it names is removed.

The general defect behind two of the three was the more interesting finding: a
batched missing-field mechanism existed and had simply not been applied across
every write op. The repair was to reuse it, not to invent a second one.

---

## 3. What is surveyed but not built

Four targets got real investigation before the budget turned, and their value is
that the next session does not re-orient from scratch:

`corpus-survey.md` and `program-docs.md` are the reusable ones — they locate the
overhaul program, the charters, and the corpus paths that a prior session
wrongly believed it needed cortex MCP to read. `manage-socket.md`,
`closeout-home.md`, `collector.md`, `scan-proxy.md`, `scan-federation.md` and
`CHANNEL.md` hold their findings.

**The `manage` double-bound socket is the one to take next.** Window 1 is closed
and the partition released, so it is now a fix rather than a report. It is cheap,
unexamined, and sits under the restart path everything else depends on.

---

## 4. Errors by this window

Three, and they are all the same error — a claim asserted where an observation
was available. Recording them because a session auditing for that class, which
omits its own instances, is committing the error it describes.

1. **The `-f` was not load-bearing.** I was handed, and nearly propagated, the
   claim that `libs/cortex_store/dispatch_ops/test_friction_batched_requirements.py`
   needed `git add -f` because it matched `.gitignore:76`'s repo-wide
   `**/test_*.py`. `git check-ignore -v` exits 1 on it — **not ignored.** That
   rule was already reversed earlier in the day. Staging explicitly was still
   right; the stated reason was false, and it had already propagated through the
   brief and an operator answer before reaching me. Relatedly, the 2,474 closeout
   sidecars are not hidden by that rule either — they are hidden by a bare `tmp/`
   at `.gitignore:47`. Two distinct causes were being read as one, which matters
   because reversing line 76 does nothing at all for the closeouts.

2. **A fix-and-test pass produced a green test that verified nothing.** Commit
   `0ab7359f` is titled "accept claim as an alias for note on the friction op."
   The alias already worked. What the commit actually changed was exception
   handling, and the effect on `test_claim_is_accepted_as_an_alias_for_note` was
   to convert an honest failure into a vacuous pass: the test's only real
   assertion sits inside `if isinstance(err, dict):`, and the call returns
   `{'error': 'Entity not found: service:probe', 'status_code': 404}` where
   `error` is a **string**. Guard false, body skipped, green. Caught by running
   the call rather than reading the report. Repaired at `a84f1977`, which now
   asserts across both error shapes and was demonstrated to go red when the
   resolution is removed.

   **The commit message is still wrong and the code is still right.** The
   exception wrap matches the same file's owner-resolution convention. Its
   correct description is *return assertion-create `HTTPException` as a dispatch
   error dict*. Recorded rather than rewritten.

3. **"Pre-existing failures" was asserted before it was checked**, and separately
   **"worktree removed" was reported while the worktree was still registered.**
   The first was subsequently settled properly — a baseline worktree at
   `369ca56b` produced an identical 60-nodeid FAILED set, so the failures do
   pre-date this window. The second I found in `git worktree list` and cleaned up.
   A second baseline worktree at `a28906fc` is **left alone**; it is not
   attributable to this window.

My own first attempt at that baseline died at collection on a `PYTHONPATH`
problem and I treated it as a dead end rather than a tooling failure. It was a
tooling failure, and a later pass got through it.

---

## 5. Residuals

**Doc-parity already names our ops, and pre-dates us.** Among the 60 pre-existing
failures, the doc-parity assertion reads `supersede/friction missing as primary op
or alias`, and ops-doc-surface reads `assert 15 == 14`. These did not come from
this window's changes — but they are about precisely the ops this window just
repaired, which means the descriptor surface does not advertise `supersede` and
`friction` correctly while their behaviour has just changed underneath it. That
adjacency makes it the most relevant of the 60 and the natural next slice. What
would settle it: read the descriptor registry against the two ops' actual
signatures.

**F-A was never settled** and remains with the human operator. The CDP operator
seat declined to invent a reading, which was correct, and endorsed the interim
judgment that these three defects are repair rather than overhaul. If F-A is
settled, whoever settles it should also **close or refresh the record** — an
unmaintained gate is how a lifted wall gets recorded as blocking twice.

**Q2 — the propagation ledger row.** Worth putting in front of the operator as
three options, not the two he was handed. The CDP seat's third shape is the right
one: a row targeting SHA X is satisfied when the service runs **X or any
descendant**, which makes being overtaken a success rather than a failure, and
removes the need for a `superseded` state entirely. Two caveats to carry with it.
Ancestry is **undefined when `code_ref` is not a commit** — window 1 found a
failed row carrying the literal `"working"` — so it needs an explicit rule for
non-commit refs rather than inheriting that gap silently. And the "it is weaker"
concession is overstated: exact equality never *detected* an unintended-newer
restart either, it just failed the row without diagnosing why. The genuine open
question is whether ancestry is cheap in the settle path, which is one function's
worth of reading rather than a research question.

---

## 6. Commits

| SHA | What |
|---|---|
| `8144f23f` | Created `CLAIMS.md` — window 1 said to append to it and read it first; it did not exist |
| `d5e7b609` | Track the 25 stargate tests a nested ignore kept invisible |
| `aebb09a5` | `assertion_update` reaches the columns the auditor gate reads |
| `733ab0a7` | Track two stargate test runner scripts the same rule hid |
| `ad14a40a` | Friction op batched requirements + stargate test fixes |
| `0ab7359f` | Return assertion-create `HTTPException` as a dispatch error dict *(message says "claim alias"; see §4.2)* |
| `8a00b2d3` | Report all missing supersede requirements in one response |
| `a84f1977` | Make the claim-alias assertion run instead of skipping |
