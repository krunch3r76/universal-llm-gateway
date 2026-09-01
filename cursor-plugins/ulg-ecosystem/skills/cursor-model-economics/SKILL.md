---
name: cursor-model-economics
description: "Cursor model economics — $/M rates, conductor T0–T3 tier table, Sonnet 5 vs Opus/GPT context billing, Grok effort card, Auto/Router entitlement. CDP + Cursor shared_sync annex."
---

# Cursor model economics

Short annex for CDP / web-anthropic / Cowork and Cursor seats. Full conductor
orchestration lives in `conductor` (`cursor_only`). Probe SOT:
`config/model_rates.yaml` ($/M) · live cards @ DESCRIPTOR_VERSION 2026-08-15.

## Costs

| Source | Role |
|---|---|
| **`config/model_rates.yaml`** | Authoritative **$/M** input/output/cache rates |
| Model cards (`libs/cursor_capabilities/`) | Knobs, variants, capability — **not** pricing |

Pinned manual seeds win over OpenRouter catalog projection.

## Fable 5.1 (2026-09-01) — cache-read cut only, verdict splits by surface

Headline $/M unchanged vs Fable 5 ($10 in / $50 out); only cache reads dropped
75% ($1→$0.25/M). Not a blanket upgrade — verdict depends on the caller's
usage shape:

| Surface | Verdict | Why |
|---|---|---|
| `cursor/claude-fable-5{,-1}` (Other Models) | **No change — block stands on cost alone** | `light-bounded`/`pure-mechanical` binds are short, low-repeat-context — no sustained cached prefix to discount; $10/$50 base still dominates |
| `cdp/fable` (claude.ai/Cowork) | **Real structural win, not just a promo** | Our usage (staged skill-floor + `--converse` N-turn) is the cache-heavy long-agentic shape the discount targets (Anthropic: ~45% cheaper on highly-agentic workloads). Shows up mainly as **weekly-usage stretch** — a cache-heavy session burns less of the shared Fable/All-models weekly pool per turn, so the same weekly cap covers more real work, independent of usage-credits mode or any temporary promo |

Lease/nesting-mechanics case for a narrow `cursor/*` Fable carve-out is
unrelated to this price change and stays a separate, open discussion.

## Conductor tier ladder (T0–T3)

Cheaper model at higher effort beats premium at default effort. **Pool first:**
Grok/Composer draw Cursor Models; Sonnet/Opus/Terra draw the capped Other
Models (second) pool. Rate-relative "Sonnet is 40% of Opus" does not matter
once the second pool is empty.

| Tier | Model | Effort / knobs | When |
|---|---|---|---|
| **T0** | omit → `cursor/composer-2.5` | — | Mechanical scoreboard drive; nest only |
| **T1** | **`cursor/grok-4.6`** | **`xhigh`** | **Standing default** — multi-G orchestrate, rank, adjudicate (Cursor Models pool) |
| **T2** | `cursor/claude-sonnet-5` | `high`, `thinking=true`, `context=300k` | Named trigger only — grok cannot hold the remit. Other Models |
| **T3** | `cursor/claude-opus-5` | full card (`low`→`max`); inform-then-proceed | Invariant-touching bind (trigger is *whether to pick T3*, not the effort rung) |

Nested legs: mechanical → Composer · investigate densify → Grok @ `xhigh`
· Other Models (Sonnet / Opus-in-cursor / Terra / Sol / Luna) only on an **explicit
pin** · `cursor/claude-fable-5{,-1}` **blocked** (cost — Fable 5.1 launched
2026-09-01 at the same headline $/M as Fable 5) — use `cdp/fable` · binder when
unsure → `judgment-escalation-ladder` (2c Terra is explicit-only; default after
Fable is Grok).

Detail + admit shapes: Use the `conductor` skill.

## Context / long-window billing

| Model | Long context |
|---|---|
| **Sonnet 5** `1m` | No long-context surcharge vs 300k — still Other Models; do not default T1 to 1m |
| **GPT-5.6** `1m` | **2× input** vs `272k` — prefer `272k` on Terra unless 1m required |
| **Opus** | Standard pool table rates |

## GPT-5.6 family knobs

Live `reasoning` enum: `none|low|medium|high|xhigh|max` — **`extra-high` is not
accepted** (use `xhigh`).

## Grok effort

Gate = model card (`libs/cursor_capabilities`): `low|medium|high|xhigh`. `fast` is a separate knob (default true; `false` is the cheaper rate row). ¬ a policy ladder below the card.

## Auto / Cursor Router

| Fact | Detail |
|---|---|
| **Product Auto** | Cursor Router / `auto-smart` = **Teams/Enterprise only** |
| **This fleet key** | Catalog has bare `default`, not `auto-smart` |
| **Router lever** | `optimize_for` when entitled — **¬ prompt-nudge** the router |
| **ULG dense work** | `desired_model=auto` **forbidden** — pin Composer; that is the **Auto lane**, not Cursor Router |

## Composes with

| Slug | Boundary |
|---|---|
| `conductor` | Full off-tick operator packet + tier admit (`cursor_only`) |
| `cdp-operator-proxy` | Operator-proxy grammar — pins `density` only |
| `lean-context-dispatch-first` | Explore-first read · dispatch ladder · Grok/Opus gates |
| `consult-routing` | Model split by surface / work class |
