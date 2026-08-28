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
pin** · `cursor/claude-fable-5` **blocked** (cost) — use `cdp/fable` · binder when
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
