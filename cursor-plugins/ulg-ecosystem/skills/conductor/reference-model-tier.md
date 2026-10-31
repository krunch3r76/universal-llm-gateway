## Model / effort tier (cost-aware — binding)
<!-- width-seat:v1:start -->
> **Width seat** (generated from `libs/implement_admission/conductor_critic_render.py`; do not hand-edit): critic = `anthropic/claude-sonnet-5-5` · `reasoning_effort=high` · usage channel **Sonnet**. SKETCH (G1) = `anthropic/claude-sonnet-5-5` · `reasoning_effort=high`. SKEPTIC (G4) = `anthropic/claude-sonnet-5-5` · `reasoning_effort=high` (cross-family review). RESTORE = `anthropic/claude-fable-5-1` · `reasoning_effort=high` (operator restore preset).
<!-- width-seat:v1:end -->


Prefer the **cheapest in-use tier that can honestly hold the conductor remit**.
Re-check when pricing or fleet defaults move (`observability` dispatch-economics
when spend matters). Compose with `lean-context-dispatch-first` + `consult-routing`.
Cursor seats in use: `cursor/grok-4.7` and `cursor/composer-2.5`.
Rates: `config/model_rates.yaml`.

| Seat | Model / contract | Use when |
|---|---|---|
| **House driver (cursor_sdk)** | **`cursor/composer-2.5`**, effort **low** (`route_policy.yaml` `workflows.auto_judgment` and `impresario.driver`; `contract_effort.conductor`); omitted knobs follow the card | Conductor start and later successor. Enumerate, drive, return `OPEN FORK:` lines; does not rank. |
| **Composer (nested implement)** | **`cursor/composer-2.5`**; omitted knobs follow the card | Mechanical G-rows and `implement` \| `pure-mechanical`. Omitting `model=` on `contract=implement` resolves `workflows.mechanical_implement` (`cursor/composer-2.5`). |
| **Width** | ACTIVE = width-seat critic `anthropic/claude-sonnet-5-5` | G2 frame and G3→G5 score-ratify read ACTIVE. G1 sketcher reads SKETCH. G4 skeptic reads SKEPTIC. Do not substitute `cdp/`. RESTORE is the width-seat RESTORE line (`anthropic/claude-fable-5-1`) and is used only when Kaywan asks. |
| **Bind / review** | **`anthropic/claude-sonnet-5-5`** (`impresario.critic`; `contract=delivery-review` when reviewing) | Bind, independent check, architecture-suitability, ≥2 co-primary unranked, invariant-touching / cross-agent bind, recurrence ≥2, external check. Do not substitute `cdp/`. Execution needs → Composer `pure-mechanical` limb. Escalate on the same prefix: `anthropic/claude-opus-5-5`, then `anthropic/claude-fable-5-1`. |
| **Live checkout** | **`cursor/grok-4.7`** `contract=freeform` | File:line depth on the cursor-sdk checkout. |


G4 is SKEPTIC, one effort rung above ACTIVE, on the width-seat critic channel. RESTORE (`anthropic/claude-fable-5-1`, only when Kaywan asks) returns the cross-family skeptic.

**Nested legs (always split by cost class):**
- Judgment nests (forks, G3/G6 reviews, open-cause diagnosis, architecture) go to the width-seat critic `anthropic/claude-sonnet-5-5`. Do not substitute `cdp/`.
- Routine nests (relays, harvests, re-reads, seeds with anchors, bounded confers) go to `cursor/composer-2.5` (`workflows.auto_judgment`).
- Mechanical and implement nests go to `cursor/composer-2.5`. Omitted knobs follow the card. Omitting `model=` on `contract=implement` resolves `workflows.mechanical_implement`.
- Investigate densify → `cursor/composer-2.5` effort high (`workflows.investigate`, `contract_effort.investigate`), `contract=investigate`, returning `OPEN FORK:` lines
- Independent binder when conductor unsure → `anthropic/claude-sonnet-5-5` (`anthropic/claude-fable-5-1` only when Kaywan asks)

**Anti-patterns (cost):**
| Bad | Good |
|---|---|
| Default the conductor off `workflows.auto_judgment` | `cursor/composer-2.5` standing; width-seat critic `anthropic/claude-sonnet-5-5` only on a named bind trigger |
| Premium model at default effort | Cheaper model at high effort **on the same pool** |
| Composer conductor that also hand-codes a mechanical remainder after a pick | Nest Composer |
| Re-spend Opus to amend a densified packet | Composer amend |
| Ignore `sdk_cost_risk` warning | Downgrade model or split bind/compose |
| Pin a cursor seat other than grok or composer | `cursor/grok-4.7` standing; `cursor/composer-2.5` for mechanical implement |
| Cite total-dollar-by-model when call volumes differ by an order of magnitude | Reprice the same token mix at both rate cards |

`/conductor` asks standing seat / pin (Q7) when unbound; operator may pin a slug.

### Profile fable-scarce

**Selector precedence:** packet profile line (`conductor_profile: fable-scarce` in
packet frontmatter) ≻ todo attr `conductor_profile=fable-scarce` ≻ house default.
Absent the selector ⇒ house G-row pickers below, not this table. This profile
does not name a different width model: G1, G2, G4, and G3→G5 read ACTIVE, and
the ≤1 `anthropic/claude-fable-5-1` leg-per-arc allowance is removed. Scoreboard rows **G2 / G4 / G6** may still
annotate `profile=fable-scarce`.

| G-row | Picker | Effort pin | Transport | `purpose` |
|---|---|---|---|---|
| **G2 Frame** | ACTIVE | reasoning_effort per ACTIVE; effort_when_bind_gates_wave per ACTIVE when a bind gates a wave. | Follow up into the live G1 CSE when that CSE ran on ACTIVE.model. Fresh `team_dispatch` of ACTIVE only when no live G1 CSE exists. | **`ask`** |
| **G4 Skeptic** | ACTIVE. | reasoning_effort per ACTIVE; effort_when_bind_gates_wave per ACTIVE when a bind gates a wave. | Same seat as G1. The ≤1 `anthropic/claude-fable-5-1` leg allowance is removed. | `ask` \| `review` |
| **G6 Pre-land review** | `anthropic/claude-sonnet-5-5` | **`high`** (`impresario.critic`); **`max` when invariant-touching** | Lane branch diff; **`review harvest ≺ land ≺ DONE`** unchanged. Do not substitute `cdp/`. | **`review`** |

**Refused effort pin** on admit ⇒ report and halt — never silent substitute to another rung or seat
(S7-I).

