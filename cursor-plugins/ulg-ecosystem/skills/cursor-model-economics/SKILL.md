---
name: cursor-model-economics
description: "Cursor model economics — $/M rates, conductor seat table, grok-4.7 and composer-2.5, Grok effort card, Auto/Router entitlement. CDP + Cursor shared_sync annex."
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
| `workflows.check_review` standing default | **Judgment model** `cursor/grok-4.7` | Operator 2026-09-27: reviewer omit-model is judgment, so it uses `workflows.auto_judgment`. Second-pool ids stay explicit pins. |
| `cdp/fable-5.1` (claude.ai/Cowork) | **Real structural win, not just a promo** | Our usage (staged skill-floor + `--converse` N-turn) is the cache-heavy long-agentic shape the discount targets (Anthropic: ~45% cheaper on highly-agentic workloads). Shows up mainly as **weekly-usage stretch** — a cache-heavy session burns less of the shared Fable/All-models weekly pool per turn, so the same weekly cap covers more real work, independent of usage-credits mode or any temporary promo |

Rates (both Fable ids): `$10 / $50 / $0.25 / $12.50` input/output/cache-read/cache-write per M — pinned in `config/model_rates.yaml`.

## Conductor seat table

Cheaper model at higher effort beats a wider seat at default effort.
Cursor seats in use: `cursor/grok-4.7` (omit-model) and `cursor/composer-2.5` (mechanical implement).

| Seat | Model / contract | Use when |
|---|---|---|
| **House driver (cursor_sdk)** | **`cursor/grok-4.7`** — omitted knobs follow the card; same slug as the ticker successor | Conductor start and later successor. Enumerate, drive, return `OPEN FORK:` lines; does not rank. |
| **Composer (nested implement)** | **`cursor/composer-2.5`** — pin `model=` on mechanical `job=implement`; `model_knobs={"fast":"true"}` | Mechanical G-rows and `implement` \| `pure-mechanical`. Omit `model=` resolves `cursor/grok-4.7`, not Composer. |
| **CDP width** | **`cdp/opus-5`** | Explore, hypotheses, Q, L0–L2, enumerate-fork resolution when forks are open-ended. `cdp/fable-5.1` only when Kaywan asks. |
| **CDP bind / review** | **`cdp/opus-5.5`** (`job=code-review` when reviewing) | Bind, independent check, architecture-suitability, ≥2 co-primary unranked, invariant-touching / cross-agent bind, recurrence ≥2, external check. Execution needs → `cursor/composer-2.5` `pure-mechanical` limb. Stronger Opus is explicit `cdp/opus-5`. |
| **Check/review** | **`cursor/grok-4.7`** via `workflows.check_review.model` | Same model as judgment omit. |
| **Live checkout** | **`cursor/grok-4.7`** `job=freeform` | File:line depth on a cursor-sdk checkout. |


Nested legs: mechanical → `cursor/composer-2.5` · investigate → `cursor/grok-4.7` `job=investigate` returning `OPEN FORK:` lines
· `cursor/claude-fable-5{,-1}` **blocked on judgment/implement** (cost) —
`cdp/fable` only when Kaywan asks · binder when unsure → `cdp/opus-5.5`.

Detail + admit shapes: Use the `conductor` skill.

## Grok effort

Gate = model card (`libs/cursor_capabilities` `grok-4.7`): `effort` `low|medium|high|xhigh`. Silence on `model_knobs` follows that card. ¬ a policy ladder below the card, and ¬ copy the card's current default into this text.

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
