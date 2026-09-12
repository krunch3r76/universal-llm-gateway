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
| `cursor/claude-fable-5-1` — **`workflows.check_review` standing default (operator window)** | **Admitted on Other Models pool** | Operator direction 2026-09-11: lean on Fable while the window favors it; code-lane merged mechanical check is the cache-amortizing shape here. **Revert = edit `route_policy.yaml` only** — never silent terra/opus substitution |
| `cdp/fable` (claude.ai/Cowork) | **Real structural win, not just a promo** | Our usage (staged skill-floor + `--converse` N-turn) is the cache-heavy long-agentic shape the discount targets (Anthropic: ~45% cheaper on highly-agentic workloads). Shows up mainly as **weekly-usage stretch** — a cache-heavy session burns less of the shared Fable/All-models weekly pool per turn, so the same weekly cap covers more real work, independent of usage-credits mode or any temporary promo |

Rates (both Fable ids): `$10 / $50 / $0.25 / $12.50` input/output/cache-read/cache-write per M — pinned in `config/model_rates.yaml`.

## Conductor seat table

Cheaper model at higher effort beats premium at default effort. **Pool first:**
Composer draws Cursor Models; Sonnet/Opus/Terra draw the capped Other
Models (second) pool. Rate-relative "Sonnet is 40% of Opus" does not matter
once the second pool is empty.

| Seat | Model / contract | Use when |
|---|---|---|
| **Composer (cursor_sdk)** | **`cursor/composer-2.5`** — omit `model=`; `model_knobs={"fast":"true"}` | The only cursor_sdk seat. Judgment vs implement is carried by `contract` (`none` \| `investigate` vs `implement` \| `pure-mechanical`), never by model. Multi-G orchestrate, scoreboard drive, enumerate (returns `OPEN FORK:` lines, never binds). |
| **CDP width** | **`cdp/fable`** | Explore, hypotheses, Q, L0–L2, enumerate-fork resolution when forks are open-ended. |
| **CDP bind / review** | **`cdp/opus-5`** (`purpose=review` when reviewing) | Bind, independent check, architecture-suitability, ≥2 co-primary unranked, invariant-touching / cross-agent bind, recurrence ≥2, external check. Execution needs → Composer `pure-mechanical` limb. |
| **Check/review (standing window)** | **`cursor/claude-fable-5-1`** via `workflows.check_review.model` | Code-lane merged mechanical check only — operator window; explicit `cursor/gpt-5.6-terra\|sol` pins remain 2c-only |
| **Explicit pins (never standing)** | `cursor/claude-opus-5` premium live-checkout (inform-then-proceed) · `cursor/gpt-5.6-terra\|sol` only when operator/packet names Other Models · `cursor/claude-sonnet-5` last resort (CDP lane unavailable) | Named per leg only — never a default outside check_review, never a tier row. |

> `cursor/claude-sonnet-5` — last resort, explicit `model=` pin only: fire when the CDP lane is unavailable and the leg cannot wait; CDP is preferred; never the first line of a recipe.

Nested legs: mechanical → Composer · investigate densify → Composer `contract=investigate` returning `OPEN FORK:` lines
· Other Models (Sonnet / Opus-in-cursor / Terra / Sol / Luna) only on an **explicit
pin** · `cursor/claude-fable-5{,-1}` **blocked on judgment/implement** (cost) —
**except** standing `check_review` window default above — use `cdp/fable` for width/bind · binder when
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
