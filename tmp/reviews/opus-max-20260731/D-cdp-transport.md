# Workstream D — CDP transport

## Question

Pinned as asked, then sharpened (did not replace the brief's question):

1. **Primary (brief §3-D):** distinguish **"compose toggle broken"** from **"session unauthenticated or awaiting / stuck on Manual approval."**
2. **Standing:** is Playwright-driving `claude.ai` the right transport for CDP seats, or is the failure rate a property of the approach?

Changed: treated (1) as the binding first question; (2) is answered only after the failure classes are separated. Also pinned two cheap second-order defects: admission-as-success reporting, and the mangled `execution_id`.

## What you found

### Verdict: b7ea437d is approval-stuck, not toggle-broken

Event service `cdp.generate.stalled` at **2026-07-31T20:08:28.905Z** for
`execution_id=b7ea437d-9e08-4977-965a-e9fe1fcd7362`:

| Field | Observed value |
|---|---|
| URL | `https://claude.ai/new` |
| Mode before | `chat` (title `New chat - Claude`, approval null) |
| Mode after | `cowork` (title `New task - Claude`) |
| Attest | `ok=true`, `step=attested_cowork` |
| Approval after | `{"aria": "Manually approve"}` |
| Hint (old code) | `new_compose_toggle_failed` ← **mislabel** |

The Chat→Cowork toggle **succeeded**. Failure is in `set_approval_mode("auto")` leaving the chip on Manual. Same Manual fingerprint on the 10:13 fire (`6590-mission-close.md`). The 10:44 fire is a **third** class: URL `claude.ai/logout` / `chip_missing` = **unauthenticated**.

### Why the fleet conflated them

`libs/claude_bundles/chat_session_hygiene.py` `_compose_setup_error` (pre-fix) always set `hint=new_compose_toggle_failed` on bare `/new`, and preferred `result["mode"]` (the **successful** block) over `result["approval"]` when both existed. So b7ea437d's error body looked like a toggle failure while embedding a successful Cowork attest.

### Failure-class inventory (2026-07-31)

| Class | Fingerprint | Examples |
|---|---|---|
| **toggle** | mode not cowork / `chip_missing` while on `/new` and authenticated | true toggle bugs only |
| **approval** | mode attested cowork, approval still `Manually approve` | b7ea437d 20:08Z; 10:13 fire |
| **unauthenticated** | URL `/login` or `/logout` | 10:44 fire after disk reclaim |
| **reconcile abandon** | `stall_stage=reconcile_abandoned`, open leg > 4200s | e5bd9575 10:38Z |
| **admission misread** | `status=running, terminal=false` relayed as live window | agent-bus:6622 / next-arc-agenda |

Intermittent success after ~12:17Z means the approach is not categorically dead — incapacity claims from one or two failures were false (brief §4).

### Playwright-on-claude.ai — transport verdict

**Keep it for the operator-proxy / Cowork product seat; do not treat today's failure rate as proof the approach is wrong.**

- Anthropic Stargate API is prohibited for this seat class; the product surface (Cowork, skills chips, Authorize-triggers, Outputs) has no equivalent API.
- Observed failures today are mostly **session/auth**, **approval-mode automation**, **reconcile timeout**, and **claim-vs-observation** — not "Playwright cannot click Cowork."
- Residual fragility is real (DOM churn, cookie seed, Manual→Auto flake). Mitigations are classification, auth preflight, bounded approval retry, and honest admission envelopes — not abandoning CDP.

### Admission-as-success

Stargate CDP generate already returns `handoff_status=awaiting_first_reply` and `terminal=false` (`cdp_generate.py` ~362–392; `handoff_response.py` `_INITIAL_HANDOFF_STATUS`). The defect was **relayability**: seats treated `status=running` as arrival. Fixed on the satellite submit path in-territory (`SubmitProjectAskResponse`). Stargate `team_dispatch` path is out of territory — still needs the same fields or an explicit `phase=admitted` if not already consumed.

### Mangled execution_id — not a code truncation site

| Source | ID |
|---|---|
| Event service / stall payload | `b7ea437d-9e08-4977-965a-e9fe1fcd7362` (correct) |
| Bus subject | `cdp FAILED — b7ea437d` (`[:8]` only — intentional) |
| `format_cdp_result_body` | full correct id |
| Assertion a:27433 claim prose | `b7ea437d-9e08-497a-e9fe1fcd7362` (malformed) |

The mangled form appears in **operator-authored claim text** (a:27433 item 6), then echoed into `next-arc-agenda.md` / this brief as "the relay mangled it." No truncation function in `services/cdp-ask/**`, `libs/cdp_ask/**`, or `libs/claude_bundles/**` produces that string. Dropping chars `7-965` from the correct UUID reproduces it — consistent with LLM UUID garbling, not `[:N]` truncation. **Closeout relay attribution is not evidenced; residual is claim-prose hygiene.**

## What you changed

| SHA | Change |
|---|---|
| `5275247ea9bbbcb94985501a6ccdd525f7677b8e` | `classify_compose_setup_failure` + auth preflight + approval retry; tests `8 passed` on classify+cowork suite |
| `0acc3e308bd8b0a35f8d335fb2ab4718355cfe04` | Submit response: `terminal=false`, `phase=admitted`, `handoff_status=awaiting_first_reply`; MCP docstring honesty; `3 passed` on submit+client |

Key loci:

- `libs/claude_bundles/chat_session_hygiene.py` — `compose_auth_failure_hint`, `classify_compose_setup_failure`, rewritten `_compose_setup_error`, auth preflight in `goto_fresh_compose`
- `libs/claude_bundles/chat_cowork_mode.py` — one bounded Manual→Auto retry in `ensure_cowork_auto`
- `libs/cdp_ask/models.py` / `app.py` — admission fields on submit
- `services/mcp-server/tools/project_ask.py` — submit docs: admission ≠ arrival

## What you did NOT change and why

- **Did not replace Playwright transport** — failure classes above do not justify abandoning the product seat.
- **Did not touch Stargate `cdp_generate.py` / `team_dispatch` admission envelope** — outside file territory; satellite path fixed; Stargate already has `handoff_status` / `terminal=false` but may still need consumer-facing `phase=admitted`.
- **Did not "fix" UUID truncation in code** — no truncation site found; mangled id is claim prose (a:27433).
- **Did not abort/retry live CDP sessions** — observe-only; operator-gated.
- **Did not restart services** — constraint; see PROPAGATION REQUIRED.
- **Did not edit** `services/git_integration_worker/**`, `frontier.py`, `config/mcp/**`, cortex_store, charter-runner, `.gitignore`.

Note: commit `5275247e` also carried already-staged `tmp/reviews/opus-max-20260731/{B,C}-*.md` from parallel workers' index (shared-checkout hazard). Content was theirs; not rewritten.

## PROPAGATION REQUIRED

| Service | Why | SHA |
|---|---|---|
| **cdp-ask** (Jupiter satellite) | Loads `libs/claude_bundles` + `libs/cdp_ask` — classification + admission fields | `0acc3e30` (includes `5275247e`) |
| **mcp-server** (hub) | `project_ask.py` docstring / relay surface | `0acc3e30` |
| **universal-stargate** (optional, out-of-territory) | If CDP generate worker imports `claude_bundles` for compose setup on hub paths; also for mirroring `phase=admitted` on `team_dispatch` | land separately; not in these SHAs |

Landed-not-live until restart. Do not `force=true` drain.

## Open questions and residuals

| Residual | What would settle it |
|---|---|
| Why Manual→Auto click flakes (product refuse vs Playwright miss vs Authorize-triggers overlay) | Dogfood one `/new` launch after propagation; if `failure_class=approval` + `stuck_manual`, screenshot approval menu; if Kaywan Authorize dialog present, that is human gate not toggle |
| Is CDP profile authenticated right now? | Navigate ask profile; URL must not be `/login`/`/logout`; cookie seed from :9222 |
| e5bd9575 / reconcile_abandoned (70 min) | Poll satellite + inflight ledger for that sat id; separate from compose setup |
| Stargate `team_dispatch` still relayable as "live"? | Read live generate 202 body post-restart; add `phase=admitted` if consumers ignore `handoff_status` |
| Operator-reachable CDP window URL (a:27433 #6) | Architecture ask — `cdp-session-tracking-arch`; not fixed here |
| a:27433 claim still cites mangled UUID | Correct assertion claim text against event-service id (outside this seat's MCP-down constraint) |

---

# Transport verdict — adversarial pass

Second pass, separate seat. Scope: **only** the standing question — *is Playwright-driving
`claude.ai` the right transport for CDP seats, or is the failure rate a property of the
approach?* The diagnosis above (approval-stuck ≠ toggle-broken, `_compose_setup_error`
mislabel, third `unauthenticated` class) is taken as given and was not re-litigated. No code
was changed in this pass.

The pass above answered the transport question in four sentences and moved on. This pass
measured it. The conclusion survives; **two of its three supporting premises do not**, and
the verdict as written does not cover the traffic that is actually failing.

## 0. The distinction the first pass collapsed

The first pass established that `b7ea437d` was mislabelled. It then wrote:

> "today's failure rate is mostly misclassified auth/approval, not proof the approach is wrong."

These are two different claims and the second does not follow from the first.
**Correcting a mislabel changes the name of a failure, not the count of failures.** A day
that produced N failures still produced N failures after they are correctly sorted. To move
from "the labels were wrong" to "the transport is sound" requires a denominator, and the
first pass never computed one — it reasoned from five hand-collected fingerprints quoted in
prose (thread turns, closeout sidecars, the brief itself).

That is the brief's own §2 defect — *a claim asserted where an observation was available* —
committed in the paragraph immediately after diagnosing it. The observation was available.
It is below.

## 1. The measurement

### Why the event service alone cannot answer this — and what can

The event service holds **1,013,665 events** spanning `2026-07-27T17:36:38Z` →
`2026-07-31T21:04:15Z`, so the incident window is fully inside retention. It contains
exactly **five** `cdp.generate.*` events:

| Signal | Count | When |
|---|---|---|
| `cdp.generate.proof` | 2 | 2026-07-30T08:11Z, 08:19Z |
| `cdp.generate.admitted` | 1 | 2026-07-31T20:08:14Z |
| `cdp.generate.submitted` | 1 | 2026-07-31T20:08:14Z |
| `cdp.generate.stalled` | 1 | 2026-07-31T20:08:28Z |
| `cdp.generate.reconciled` | 0 | — |
| `cdp.generate.delivery_failed` | 0 | — |

**All six emit sites exist** (`services/universal-stargate/systems/frontier_consult/cdp_events.py:19–123`).
Between `2026-07-30T09:42Z` and `2026-07-31T19:51Z` — a 35-hour span in which **67 CDP legs
ran** — the event service recorded **zero** `cdp.generate.*` events. Emission resumes at
`20:08Z`, sixteen minutes after the running Stargate started (`pid 1173254`, started
`2026-07-31T19:51:44Z`). Cause not established; the observation is that CDP event emission
is **not continuous across Stargate process generations**, so the fleet's designated primary
observability surface covered **1.5% (1/68)** of this subsystem's traffic during the period
everyone was reasoning about. That is the mechanical reason the fleet argued from prose.

Two surfaces *did* record it, and together they are sufficient:

- **Denominator** — `cdp_inflight_leg` in the Stargate CDP in-flight ledger. One row per
  admitted leg (`cdp_generate.py:292`), never deleted per-leg (only `clear_inflight_ledger()`
  exists, `cdp_generate_inflight_ledger.py:255`).
- **Numerator** — the on-behalf bus turn, `from_agent=cdp`, subject
  `cdp reply — <id8>` vs `cdp FAILED — <id8>`, body carrying `stall_stage` and `error`
  (`~/.agent-bus/messages.db`).

> **Ledger trap, worth recording.** The running Stargate has `DATA_DIR=/tmp`, so the live
> ledger is `/tmp/stargate-cdp-generate-inflight.db` (68 rows). The path a reader would
> naturally try, `~/.gateway/stargate-cdp-generate-inflight.db`, exists, is schema-identical,
> and holds **zero rows** with an mtime predating the incident. Querying the obvious path
> returns an empty table and invites exactly one conclusion — "there is no data" — which is
> false. Analysis method: `/tmp/d-cdp-rate-analysis.py` (read-only, not repo code).

### Rate

Window `2026-07-30T09:42:50Z` → `2026-07-31T20:08:14Z` (34.4 h), **68 admitted legs**:

| Outcome | Legs | Share |
|---|---|---|
| `cdp reply` (delivered success) | 44 | 64.7% |
| `cdp FAILED` | **17** | **25.0%** |
| terminal reached, no delivery turn | 7 | 10.3% |

The 7 undelivered legs are **successes, not silent failures**. Two independent lines: (a)
`proof_emitted=1` fires on proof *or* stall, and all 17 observed stalls produced a `cdp FAILED`
turn, so proof-without-FAILED implies success; (b) each of the 7 has caller-side bus turns
minutes later recording harvest ("*W3 G2 harvested · FRAME-READY · cdp/opus-5*",
"*G5b2 harvest complete*"). They are a **delivery-instrumentation** defect — `mark_delivered`
never recorded for 10% of legs while the caller got its answer by polling.

**The rate is not flat, and this is the finding that breaks the first pass's reasoning:**

| Window | Legs | Failed | Rate |
|---|---|---|---|
| 2026-07-30 (from 09:42Z) | 31 | 1 | **3.2%** |
| 2026-07-31 00:00–08:00Z | 21 | 5 | 23.8% |
| 2026-07-31 08:00–20:08Z | 16 | 11 | **68.8%** |
| 2026-07-31 (whole day) | 37 | 16 | 43.2% |

A 3% day and a 43% day, same transport, same code, ~34 hours apart. **Misclassification cannot
produce a 13× change in count.** Something regressed on 07-31. The first pass explained a real
regression away as a labelling artifact — and that explanation, had it stood, would have closed
investigation on a live 69% failure rate.

### Class breakdown of the 17 failures

Read from `stall_stage` + `error` in each failure's bus body:

| Class | n | Share | Fingerprint | Models |
|---|---|---|---|---|
| **`wall_clock_exceeded`** | **7** | **41.2%** | `CDP generate exceeded max_wall_s=1800` | **opus-5 ×7, fable ×0** |
| **unauthenticated** | 4 | 23.5% | `chip_missing`, url `/logout` or `/login?from=logout` | fable ×3, opus-5 ×1 |
| **approval** | 2 | 11.8% | `attest ok=true, step=attested_cowork`, chip `Manually approve` | opus-5 ×1, fable ×1 |
| `reconcile_abandoned` | 1 | 5.9% | `open leg exceeded max_open_leg_s=4200` | opus-5 |
| browser attach | 1 | 5.9% | `Chrome on :9273 did not reach CDP in 20s` | fable |
| `no_progress` | 1 | 5.9% | `project-ask HTTP 404` | opus-5 |
| aborted, no `stall_stage` | 1 | 5.9% | `error: aborted`, cause not recorded | opus-5 |
| **toggle** | **0** | **0%** | — | — |

By model: opus-5 **12/39 (30.8%)**, fable **5/28 (17.9%)**, fable-5 0/1.

Three results the first pass did not have:

1. **Zero toggle failures in 68 legs.** The mislabel finding is not merely confirmed, it is
   total: `todo:cdp-fable-compose-toggle-failure` names a class with **no observed instances**
   in the entire measurable window. It should be closed as *not-a-defect*, not as *fixed*.
2. **The approval class the first pass centred its verdict on is 2 of 17 — 12%.** It is the
   smallest non-singleton class.
3. **The plurality class is `wall_clock_exceeded`, 41%, and the first pass does not mention it
   anywhere.** Its failure-class inventory omits the single largest class.

### What `wall_clock_exceeded` actually is

`libs/claude_bundles/cdp_model_endpoint.py:523–541`: the poll loop compares elapsed against
`max_wall_s` and, on exceedance, calls `_abort_then_sweep` — **we kill the session ourselves**
and report it as a CDP failure. `DEFAULT_MAX_WALL_S = 1800` (`:27`), uniform across models.
Every one of the 7 is `cdp/opus-5` at `max_wall_s=1800`; **no fable leg ever hit it.**

So the plurality failure class is *our own thirty-minute budget*, applied uniformly to a model
whose Cowork tasks routinely run longer. On its face that is a configuration decision, wholly
independent of Playwright — which is why the transport verdict survives.

**But that reading is not established, and the gap is the crux of this pass.** A progress
fingerprint *is* computed every poll (`:571–574`, `fp != last_fp` → reset `last_progress_at`),
yet `last_progress_at` is consulted **only inside the poll-error branch** (`:549–550`,
`if snapshot.get("error") and "status" not in snapshot`). A session that polls cleanly forever
without advancing therefore **never** trips `no_progress`; it runs the full 1800 s and is
reported as `wall_clock_exceeded`. The label cannot distinguish:

- **a genuinely long task** we cut off too early → budget misconfiguration, not the transport; or
- **a silently dead browser session** still returning a well-formed `status` → squarely a
  transport property.

The datum that separates them is computed in-process every poll and thrown away. This is the
brief's §2 thesis in its purest form: the system **asserts** elapsed-time exhaustion when it
**holds** the observation that would name the actual cause.

## 2. Verdict

**Keep Playwright for the Cowork/operator-proxy seat — ratified, and now on evidence the first
pass did not have. But the verdict is scoped to a seat that produced almost none of the measured
failures, and its two supporting premises are wrong.**

Clause by clause against the first pass's sentence:

| Clause | Status |
|---|---|
| "Keep Playwright for the Cowork/operator-proxy seat" | **RATIFIED** — and strengthened |
| "no allowed API equivalent" | **OVERTURNED as stated** — true for Cowork affordances and Fable; false for the Opus generate lane, which is 57% of traffic and 71% of failures |
| "today's rate is mostly misclassified auth/approval" | **OVERTURNED on measurement** — approval is 12%; the plurality is a self-imposed timeout; and a 3%→43% step change is a regression, not a labelling artifact |

**What ratifies it.** 2026-07-30 ran **31 legs at 3.2% failure** on the same transport and the
same code. That is affirmative evidence the approach can work, and it is a far better argument
than "no API equivalent" — an argument from absence of alternatives, which establishes only that
we are stuck with Playwright, never that Playwright is *right*. A 97% day establishes a
capability ceiling that no amount of premise-about-alternatives could.

**What narrows it.** All 68 measured legs are the **`cdp generate` sealed one-shot lane**:
every `prompt_uri` is
`cortex://notes/system/ephemeral/cdp-endpoint/<execution_id>/prompt.md` — stage a prompt file,
submit, poll for a body, deliver one bus turn. Prompt in, text out. **No Cowork product
affordance is exercised anywhere in this population** — no Outputs, no skills chips, no
Authorize-triggers, no interactive converse. Meanwhile `mcp.project_ask.submit` — the actual
operator-proxy/converse transport — fired **twice** in the same store.

So the verdict names the Cowork seat, and the failures are in the consult lane. Those are
different workloads with different requirements, and only one of them needs a browser.

## 3. The "no allowed API equivalent" premise

Verified rather than accepted. It is **load-bearing and it decomposes into three constraints
that decay differently**:

| Surface | Constraint type | Status |
|---|---|---|
| **Cowork product** (Outputs, skills chips, Authorize-triggers, agentic tasks) | **Technical** | Real. No public API. Playwright is the only access. Durable. |
| **Fable** | **Technical (API) + policy (cursor)** | No Fable API endpoint. Excluded from `cursor/*` by house rule, though a Fable slug does exist in the Cursor harness — so the cursor half is policy, not absence. |
| **Opus, sealed consult** | **Policy, over economic, over a working path** | Not a technical constraint at all. |

The Opus row is the one that matters, because opus-5 is 39/68 legs and 12/17 failures.
`anthropic-dispatch-authorization_ulg.mdc:44` is explicit that the route **works** and is gated,
not absent: *"Stargate still hard-gates explicit `model=anthropic/*` via `anthropic_override_gate`
(`cost_intent=deliberate_high_cost` + reason, in-family profile, or review-child spawn).
**Passing that gate ≠ house-rule permission.**"* The prohibition is a **house rule** with a named
author and date (operator 2026-07-17 / 2026-07-18), sitting on top of a **cost gate**, sitting on
top of a **functioning API path**.

And there is an **allowed, non-Playwright, already-authorized** path to Opus-class reasoning
today: `seat=cursor-sdk model=cursor/claude-opus-5`, permitted under the same rule
(*"Codebase must be navigable on the executor → `cursor/claude-opus-*` (cursor-sdk) is
acceptable"*) and inform-then-proceed under `lean-context-dispatch-first`.

**Therefore the honest verdict is the one the brief anticipated:** *right transport for the
Cowork seat; for the Opus sealed-consult lane it is the right transport **given a constraint that
is policy, not physics** — and that constraint is worth revisiting on its own terms, because it
is currently routing 57% of CDP traffic and 71% of CDP failures through a browser to reach a
model that is reachable three other ways.* Policy constraints decay when someone re-reads them;
technical ones do not. Conflating the two is how a house rule becomes an architecture.

To be clear about what this does **not** license: nothing here argues for `anthropic/*`. The
substrate wall is the operator's to move. The finding is narrower and firmer — **"no allowed API
equivalent" is false as written for the majority of the failing traffic**, and a verdict resting
on it rests on a premise that does not hold there.

## 4. The strongest case against this verdict, answered

**The counter.** *"Your ratification rests on the 3.2% baseline day, and you cannot show the two
days ran comparable work. 07-31 was the fleet-review mission — longer prompts, deeper research,
heavier Cowork sessions. If 07-31's legs were genuinely harder, then `wall_clock_exceeded` is not
a budget misconfiguration at all: it is the transport failing to sustain long sessions, which is
exactly what browser-driving a third-party product UI should be expected to do, since that UI has
its own idle timers, refresh behaviour, and session eviction. You classified 41% of failures as
'not the transport's fault' while conceding you cannot tell whether those sessions were still
progressing. If they were silently dead, then wall-clock (7) plus reconcile-abandon (1) plus
browser-attach (1) plus unauthenticated (4) = 13 of 17, and the failure mode is the approach."*

**This is the right objection and I cannot fully refute it.** Three honest responses:

1. **The load-bearing part is conceded.** I cannot currently distinguish a long task from a hung
   session, for the reason given in §1 — the discriminating observable is computed and discarded.
   Any claim I made that wall-clock exceedance is *definitely* budget-only would be the exact
   defect this session exists to correct. I am not making it.
2. **The model asymmetry is hard for the counter to explain.** All 7 wall-clock failures are
   opus-5; **zero** of 29 fable/fable-5 legs hit the wall. Both run the same Playwright, same
   satellite, same Chrome, mostly the same 1800 s budget. If long sessions died from browser or
   product-UI fragility, fable's 1800 s legs should die too. A clean split along *model* rather
   than *transport* points at task duration, not session decay. It does not prove it — opus tasks
   are longer, so opus is exposed to any duration-dependent failure first — but the counter has to
   explain why a duration-dependent transport defect spared an entire model class.
3. **Even granting the counter in full, the verdict shifts rather than inverts.** The 3.2% day
   still happened, at 31 legs. A transport that cannot sustain long sessions is still the right
   transport for the Cowork seat, which has no alternative — it would simply mean long sealed
   consults should not run on it, which is the §3 conclusion by a different road.

**Second counter, briefly.** *"Session auth death is inherent to browser-driving — a cookie
profile can log out; an API key cannot."* Granted, and this is the one class that is
unambiguously a property of the approach: 4/17, 23.5%. Two things bound it. All four fell in a
single 73-minute window (10:37:18Z → 11:50:13Z) — **one incident, not four independent failures**,
which matters because it means the 25% rate counts correlated events as independent trials and
overstates steady-state risk. And it is now cheaply detectable: the auth preflight landed in
`5275247e` converts a 20-second Playwright flail into an immediate, correctly-named refusal.
Inherent, bounded, detectable — that is a cost of the approach, not a refutation of it.

## 5. What would falsify this verdict, and the measurement that settles it

**Falsifier — decisive, cheap, and already 90% implemented.** Record the progress-fingerprint
timeline on legs that hit `max_wall_s`. Then:

- **Fingerprint still advancing at abort** → the task was alive and we cut it off. Budget
  misconfiguration. Verdict holds; fix is a per-model wall.
- **Fingerprint frozen for a long tail before abort** → the session died silently while
  continuing to return well-formed `status`. That is 41% of failures being transport decay, my
  §2 reasoning collapses into the §4 counter, and the correct response is to shrink Playwright's
  blast radius — move sealed Opus consults to `cursor/claude-opus-5` and keep the browser for
  Cowork-only affordances.

One instrumented day, or a replay of the 7 known executions, settles it. **Until it is measured,
neither this verdict nor the first pass's is fully earned — and that is the accurate state, not a
hedge.**

Secondary falsifiers:

| Claim | Falsified by |
|---|---|
| Toggle class does not exist | Any leg with `mode != cowork` on `/new` while authenticated |
| 3.2% baseline is representative | Ledger reconstruction over a longer window showing 07-30 was the outlier and 25–40% is normal |
| Opus sealed consults need no Cowork affordance | Any generate-lane prompt requiring Outputs, chips, or Authorize-triggers |
| Auth deaths are incident-shaped, not steady | Auth failures recurring at similar rate across many separated windows |

**Standing caution against a false-incapacity claim** (brief §4): nothing here supports "CDP is
broken" or "Playwright cannot do this." The measured system produced **51 of 68 legs successfully
(75%)**, including a 31-leg day at 96.8%. Any future seat citing this section to justify not
trying CDP is misreading it.

## 6. Code changes recommended, NOT made

No code was edited in this pass. Precise, in dependency order:

| # | Change | Locus | Why |
|---|---|---|---|
| **1** | On `wall_clock_exceeded`, carry `polls`, `last_progress_at`, and fingerprint age into result `extras`; add a clean-poll stall detector (fingerprint unchanged > N seconds while polling OK) reporting a distinct `stalled_no_progress` | `libs/claude_bundles/cdp_model_endpoint.py:523–541`, `:571–574`; error-path check at `:549–550` | **Highest value in this document.** Settles §5. Splits the plurality class into budget vs session-death. Datum already computed. |
| **2** | Make the wall budget per-model or caller-declared; do **not** simply raise it | `DEFAULT_MAX_WALL_S = 1800`, `cdp_model_endpoint.py:27` | opus-5 hits it at 17.9%, fable at 0%. **Sequenced after #1** — raising it first would convert a visible failure into a silent 60-minute hang. |
| **3** | Report the running commit SHA from `cdp-ask` `/health` | `services/cdp-ask` | `http://jupiter:8770/health` returns `{"status":"ok","harvest_root":…}` — liveness, no version. `/version`, `/status`, `/healthz` all 404. The fleet's own propagation rule (`git merge-base --is-ancestor <sha> <running_sha>`) is **inapplicable to the one service whose staleness caused today's mislabels.** |
| **4** | Move the CDP in-flight ledger off `/tmp` | `_db_path()`, `cdp_generate_inflight_ledger.py:48–51`; running Stargate has `DATA_DIR=/tmp` | The only surface that can produce a CDP denominator, and the input to `reconcile_abandoned`, does not survive reboot or tmp-clean. The stale empty `~/.gateway` copy actively misleads. |
| **5** | Fix or explain `delivered=0` with terminal proof — 7/68 (10.3%) | `mark_delivered` call path, `cdp_generate_inflight_ledger.py:187` | Callers harvested out-of-band, so no user-visible loss today, but 10% of legs have no on-behalf delivery record. |
| **6** | Establish why `cdp.generate.*` emission stopped for 35 h / 67 legs and resumed at Stargate restart | `cdp_events.py:19–123` | Cause **not established** by this pass. Until fixed, the event service is not a usable observability surface for this lane; the ledger + bus turns are. |
| **7** | Close `todo:cdp-fable-compose-toggle-failure` as **not-a-defect**, not as fixed | ticket | Zero instances in 68 legs. Closing it "fixed" leaves a phantom class future seats will re-diagnose. |

Non-code residual: the §3 policy question — whether sealed Opus consults should keep routing
through a browser — is an **operator bind**, not an engineering change. Recorded here, not acted on.

## 7. Propagation

This pass changed no code, so it adds nothing to the PROPAGATION REQUIRED table above.

**Correction to one propagation-adjacent point.** `b7ea437d` (20:08:28Z) still shows the old
`hint: new_compose_toggle_failed` and carries no `failure_class`. That is **not** evidence the fix
is inert — `5275247e` was committed at `2026-07-31T21:00:50Z`, **52 minutes after** the leg fired.
Correct chronology, not stale code.

**The propagation status of `5275247e` on the `cdp-ask` satellite is UNVERIFIED.** The satellite
is reachable and healthy (`http://jupiter:8770/health` → `{"status":"ok"}`) but exposes **no
version or commit SHA**, so its running code cannot be compared against the fix — see
recommendation #3. No CDP leg has fired since `20:08:14Z`, so no post-fix evidence exists either
way. Recording this as unverified rather than assumed, per the standing rule that an unobserved
propagation claim is the defect this session exists to fix.
