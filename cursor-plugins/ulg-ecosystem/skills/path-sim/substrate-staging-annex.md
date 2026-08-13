# Path-sim L3 annex — substrate house rules, vision-align, docstring AC, staging

**Parent SOT:** `cursor-plugins/ulg-ecosystem/skills/path-sim/SKILL.md`.
Open this annex when **choosing a transport** for a path-sim leg, filling a
`VISION-ALIGN` block with a non-`none` verdict, or auditing v0 staging status.
L2 carries the one-line substrate rule and the VISION-ALIGN block grammar.

## Per-family parameters (effort tiers — NOT transport binds)

**Do not read this table as “who fires Q.”** Transport binds live in annex A § Dispatch bindings
and the xAI/Anthropic rows below. A `cdp/opus-5` event on a path-sim thread is usually
**R-admit** (default-on after Stage-A), not L0 Q — e.g. agent-bus:6178
`cdp.generate.admitted` @ 08:29:14Z on thread 6178 while Q ran `cursor/grok-4.6` on worker
thread 6179 @ 08:23:27Z (`frontier.sdk.generate.requested`).

| Window | Q effort (when Fable Q fires) | A effort | R-admit effort |
|---|---|---|---|
| Fable remaining (≤~1 week from 2026-07-16) | Fable 5 Max | Grok-4.5 High (transport bind) | Opus 5 (CDP) |
| Post-Fable (no pay-for-usage Fable) | Fable unavailable ⇒ **Q-CASCADE fallback** Opus 5 Max (rare) | Grok-4.5 High | Opus 5 (CDP) |

- **Default bundled Q transport:** CDP Fable (`cdp/fable`) — operator 2026-07-28 (a:26714). Grok Q only under closed detent or explicit skip.
- **Default A transport:** `cursor/grok-4.6` on cursor-sdk — unchanged across Fable window.
- **Default R-admit transport:** CDP Opus (`cdp/opus-5`) — **always Opus on bundled arcs**; this is the common Opus dispatch in event history, not L0.
- **Fable→Opus (cascade principle):** greater pass explores question space; lesser pass answers under narrower aperture — implemented as **Fable Q → Grok A → Opus R-admit**, not Opus-for-Q by default.
- **Post-Fable Q fallback only:** when Fable is unavailable, Q-CASCADE may sharpen via Opus Max (annex A § Q-cascade) — not the standing bundled default.
- Window params carry `operator ratify 2026-07-16 (a:24764)`; they name **effort**, not dispatch seat. ¬ rewrite annex A transport binds.

## Anthropic-family substrate (house rule — operator 2026-07-18)

Window params above name **quality** (Opus/Fable/Grok). Transport is separate (`decision:anthropic-family-dispatch-substrate`):

| Path | Default |
|---|---|
| `team_dispatch` `model=anthropic/*` (Stargate API) | **PROHIBITED** |
| `seat=cursor-sdk` `model=cursor/*` | **OK** except **Fable** |
| Anthropic-family wide consult / R | **web-anthropic-cdp preferred** (cortex-packaged corpus; Use the `claude-ai-cdp-navigation` skill) |
| Need live codebase navigation | **`cursor/claude-opus-*`** acceptable |

¬ equate “Opus High” / “Fable Max” with `anthropic/*`. ¬ unlock API via routine `cost_intent`. Detail: `consult-routing` § Anthropic-family substrate.

## xAI coding-substrate (house rule — operator 2026-07-18, friction 25081)

Window params above name **quality** (Grok-4.5 High). Transport on the code lane is separate:

| Path | Default |
|---|---|
| Path-sim **A** (L1+L2) / coding-lane Grok judgment / closed-detent light consult | **`seat=cursor-sdk, model=cursor/grok-4.6, contract=light-bounded`** |
| Path-sim bundled **Q** (L0) | **CDP Fable** — `team_dispatch(model=cdp/fable)` / `project_ask` `fable-5` (annex A); Grok Q only under closed detent or explicit skip |
| `team_dispatch` `role=artisan, model=xai/grok-4.6` for checkout-present coding consult | **PROHIBITED** |
| Engineering axis-2 skeptic (specs / design) | **OK** — `xai/grok-4.6` |
| Writing / correspondence / outbound prose | **PROHIBITED** for Grok — Terra+Gemini (or lead/web); `openai/gpt-5.5` operator-gated (`consult-routing` § Writing consult substrate) |
| Quality tier "Grok-4.5 High" | Names effort — transport = cursor-sdk on code lane |

Detail: `consult-routing` § xAI coding-substrate · `cortex://notes/system/specs/grok-coding-consult-substrate.md`.

## Vision-align flag (G4) — R-admit machinery

Block grammar, verdict semantics, corpus rule, note rule, and surface-glob table:
**`cortex://notes/system/specs/vision-align-grammar.md`** (shared SoT). L2 carries the
one-line emit rule and example block.

∀ durable path-sim **Q / A / R** sidecar: emit a `VISION-ALIGN` block in the footer
(alongside the conformance checklist).

### Substrate-shaping trigger (R-admit — mechanical)

**Trigger fires iff** `files_expected` (or the R-admit diff file list) ∩ surface-glob
table (shared grammar doc §5) ≠ ∅. Matching is mechanical path intersection — no NL
judgment in the mechanism.

### R-admit pillar disposition (when trigger fires)

When substrate-shaping trigger fires, R-admit sidecar MUST carry a typed line:

```
pillar_disposition: <pillars[].id member> | n/a — <reason>
```

**RETURN condition:** `pillar_disposition:` absent **or** value is neither
`n/a — <reason>` nor a **member** of `pillars[].id` from GET
`/api/v1/doctrine/vision-digest` ⇒ verdict **RETURN**.

**Membership is mechanical only** — cite ∈ `pillars[].id` **as served** (today
`{pillar-1, pillar-2, pillar-3, pillar-4, pillar-5}`; pillar-5 = event plane, added
2026-08-05). The served digest is the enum, ¬ this literal — when the two disagree the
digest wins and this line takes the patch. Aptness of the cite is ordinary seat
contradiction; a wrong-but-present member id does **not** auto-RETURN (symmetric with
`vision_field_missing` presence-only gate at admit).

**Rubric pull:** GET `/api/v1/doctrine/vision-digest`; quote the cited pillar's
`law_verbatim` and every `must_not_redecide[]` entry **verbatim** into the challenge
row. Annotate `map_sha256`; when `stale: true`, mark stale on the sidecar. No paraphrase.

Challenge row shape (R-admit sidecar, when trigger fires):

```
## Pillar disposition challenge (substrate-shaping)
trigger_globs: [<matched rows>]
pillar_disposition: pillar-N
digest_map_sha256: <map_sha256> · stale: <true|false>
law_verbatim: "<verbatim from digest>"
must_not_redecide:
  - "<verbatim>"
  - "<verbatim>"
```

## Docstring AC — A densify / R challenge (design-time) + R-after scan

AC at admit-time, scan after ship.

| Surface | Job |
|---|---|
| **A / Gate-2** | When bind adds **public** module/class/function surface → project docstring conformance into dense-spec `acceptance_criteria` |
| **R-admit** | Challenge — public-surface bind ∧ AC silent on docstrings → `ADMIT_WITH_AMENDMENTS` or `RETURN`. ¬ scan. |
| **R-after** / lead closeout | `scripts/docstring-quality` on `files_expected` — **criticals=0** or return; CDP `/docstring-enhance` when warnings starve feedstock |

**Trigger (AC required):** recommended bind creates or materially changes public Python APIs that feed arch-doc / RAG / overhaul inventory. Pure private helpers / test-only → justified omit (state why in A sidecar).

Bar + CLI: Use the `docstring-quality` skill. R-after cites scan path/exit in the review sidecar.

**Outside path-sim:** same **criticals=0** ship gate still binds — Use the `docstring-quality` skill § Ship gate · `implement-todo` §5. Path-sim pins do not waive non-path-sim closes.

## Runtime/tool claim verification (standing given — no path-sim footer)

Verifying that a **claimed** tool·service·runtime outcome actually happened is
**not** a path-sim-specific gate — it is already binding fleet-wide via
`completion-provenance-discipline` (quote the concrete tool-response payload),
presence-discipline **P3** (evidence before any done-claim), and the event-service
techniques in `debug-with-events` / agent-bus Tool Execution Verification /
`mcp-debugging-ux` (no raw SQL in operator chat). When a path-sim turn asserts a
runtime/MCP outcome, satisfy those standing disciplines
(`observability(verify-tool-execution)` / `scripts/query-events`) — do **not**
emit a ceremonial per-turn footer block. The former `EVENTS-PROBE` footer was
retired 2026-07-21 (verification is a given, not a path-sim invention); design-time
event add/prune opportunity-find also does **not** live in path-sim (retired Event
Coverage — see `todo:path-sim-remove-event-coverage`; write-time discipline →
`event-instrumentation-discipline` skill; prune/load → `todo:event-server-top-signal-prune`;
overhaul opportunity → `todo:overhaul-event-opportunity-noise-profile`).

## Delivery lanes (hybrid — no single point of failure)

| Lane | Mechanism | Use |
|---|---|---|
| Primary | explicit slug line (scope-lock field 5) | resident seats that self-fetch skills |
| Safety net | the skill's `description` trigger | seat surfaces skill by description match |
| Fallback | paste the template annex block | non-resident / API-dispatch seats that cannot fetch (e.g. claude.ai until bundle-synced) |

## Staging (keyed to window milestones)

| Stage | When | Action | Marker |
|---|---|---|---|
| 0 | bind | architecture bound provisionally on decision entity | provisional |
| 1 | this pass | v0 draft + template demoted to pointer+annex (same commit) | v0-provisional |
| 2 | window reps | every consult uses the handshake packet; checklist logged per turn; ≥2 reps, ≥1 via a dispatch/non-lead lane | evidence accrual |
| 3 | window close | ratify/revise v0 vs logged evidence; run the inheritance test at `detent=standard` on Opus | ratified or revised |

Revision triggers (any → revise before Stage 3): checklist miss rate >1/3 turns · slug line forgotten in any hand-composed packet · skill fetch fails on a resident seat · a detent proves unpinnable in one line.

## Grounding (L3)

Tree-of-Thoughts framing (branching factor ↔ diversity; deepen-past-one-hop; converge-last; ≤2-sub-parts falsifier). Corpus anchors and per-paper citations live in the template annex (`notes/system/templates/fable-path-sim-prompt.md` § Corpus grounding) and `decision:fable-path-sim-remaining-window`; not restated here.
