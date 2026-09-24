---
name: cursor-model-economics
description: "Cursor model economics — $/M rates, conductor seat table, Sonnet 5 vs Opus/GPT context billing, Grok effort card, Auto/Router entitlement. CDP + Cursor shared_sync annex."
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
| `cursor/claude-fable-5{,-1}` (Other Models) — judgment / `none` / implement binds | **Block stands on cost alone** | Short, low-repeat-context — no sustained cached prefix to discount; $10/$50 base still dominates |
| `workflows.check_review` standing default | **Primary pool** `cursor/composer-2.5` | Operator 2026-09-22: the Other Models window is closed. Second-pool ids are explicit pins only. A switch that uses the second pool when usage is open is not built. |
| `cdp/fable-5.1` (claude.ai/Cowork) | **Real structural win, not just a promo** | Our usage (staged skill-floor + `--converse` N-turn) is the cache-heavy long-agentic shape the discount targets (Anthropic: ~45% cheaper on highly-agentic workloads). Shows up mainly as **weekly-usage stretch** — a cache-heavy session burns less of the shared Fable/All-models weekly pool per turn, so the same weekly cap covers more real work, independent of usage-credits mode or any temporary promo |

Rates (both Fable ids): `$10 / $50 / $0.25 / $12.50` input/output/cache-read/cache-write per M — pinned in `config/model_rates.yaml`.

## Conductor seat table

Cheaper model at higher effort beats premium at default effort. **Pool first:**
Composer draws Cursor Models; Sonnet/Opus/Terra draw the capped Other
Models (second) pool. Rate-relative "Sonnet is 40% of Opus" does not matter
once the second pool is empty.

| Seat | Model / contract | Use when |
|---|---|---|
| **House driver (cursor_sdk)** | **`cursor/grok-4.7`** — `effort=high`, `fast=false`; same slug as the ticker successor | Conductor start and later successor. Enumerate, drive, return `OPEN FORK:` lines; does not rank. |
| **Composer (nested implement)** | **`cursor/composer-2.5`** — omit `model=`; `model_knobs={"fast":"true"}` | Mechanical G-rows and `implement` \| `pure-mechanical`. |
| **CDP width** | **`cdp/fable-5.1`** | Explore, hypotheses, Q, L0–L2, enumerate-fork resolution when forks are open-ended. Stronger Fable is explicit `cdp/fable-5`. |
| **CDP bind / review** | **`cdp/opus-5.5`** (`purpose=review` when reviewing) | Bind, independent check, architecture-suitability, ≥2 co-primary unranked, invariant-touching / cross-agent bind, recurrence ≥2, external check. Execution needs → Composer `pure-mechanical` limb. Stronger Opus is explicit `cdp/opus-5`. |
| **Check/review** | **`cursor/composer-2.5`** via `workflows.check_review.model` | Primary pool. Second-pool pins (`terra`/`sol`/`luna`/`muse`/`fable`) only when the operator names them |
| **Explicit pins (never standing)** | `cursor/claude-opus-5-5` premium live-checkout (inform-then-proceed) · `cursor/gpt-5.6-terra\|sol` only when operator/packet names Other Models · `cursor/claude-sonnet-5` last resort (CDP lane unavailable) | Named per leg only — never a standing default, never a tier row. |

> `cursor/claude-sonnet-5` — last resort, explicit `model=` pin only: fire when the CDP lane is unavailable and the leg cannot wait; CDP is preferred; never the first line of a recipe.

Nested legs: mechanical → Composer · investigate densify → Composer `contract=investigate` returning `OPEN FORK:` lines
· Other Models (Sonnet / Opus-in-cursor / Terra / Sol / Luna / Muse / Fable) only on an **explicit
pin** · `cursor/claude-fable-5{,-1}` **blocked on judgment/implement** (cost) —
use `cdp/fable-5.1` for width/bind · binder when
unsure → CDP per trigger list (2c Terra is explicit-only).

Detail + admit shapes: Use the `conductor` skill.

## Context / long-window billing

| Model | Long context |
|---|---|
| **Sonnet 5** `1m` | No long-context surcharge vs 300k — still Other Models; Sonnet is a last-resort pin; 1m is its card default |
| **GPT-5.6** `1m` | **2× input** vs `272k` — prefer `272k` on Terra unless 1m required |
| **Opus** | Standard pool table rates |

## GPT-5.6 family knobs

Live `reasoning` enum: `none|low|medium|high|xhigh|max` — **`extra-high` is not
accepted** (use `xhigh`).

## Grok effort

Gate = model card (`libs/cursor_capabilities`): `low|medium|high|xhigh`. `fast` is a separate knob: ULG omit-path / card default is **`false`** (Standard $/M). Fast (`true`) is **2×** those rates and requires `model_knobs={"fast":"true"}` — silence is Standard, not Fast. ¬ a policy ladder below the card.

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
