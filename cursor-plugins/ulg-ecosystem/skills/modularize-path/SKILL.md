---
name: modularize-path
description: "Oversized file → package directory split: CDP arch → Grok densify → Composer implement. Use on /modularize, SLOC red/yellow, file→modules package-shadow."
lifecycle: active
trigger_match_terms:
  - modularize-path
  - /modularize
  - modularize
  - package-shadow
  - file to package
  - M-Arch
  - modularize cascade
related_skills:
  - modularize-discipline
  - architecture-invariants
  - ulg-architecture
  - consult-routing
  - claude-ai-cdp-navigation
  - work-item-seed-path
  - abstraction-layering
---

# Modularize path

`∀ oversized source file: file → package directory (package-shadow)`. Peer shape to
`work-item-seed-path` / `/layer`, specialized for modularization — ¬ a todo mint,
¬ abstract SLOC diet.

Thin command: `/modularize` (plugin). Discipline floor: `modularize-discipline`.

## Root purpose (BINDING)

Success shape is a **module directory**, not sibling helper files:

| Before | After |
|---|---|
| `…/handler.py` | `…/handler/` with focused modules + `__init__.py` public surface |
| SLOC red/yellow on one path | Each new module ≤300 SLOC; forbidden names banned |

SLOC/SRP are **acceptance criteria** for the package split. Every M-Arch / densify /
implement artifact MUST name `target_package_dir` and the file→modules map.
Same-dir sibling extracts without a package are incomplete unless arch explicitly
binds that weaker shape (rare).

## Invariant

```
source_file → M0…M-Verify → package_dir
M-Arch (red/complex) ⇒ skill_floor_inlined ≺ densify
M-Arch claim ⇒ same_turn(admit(execution_id,poll_hint) ∨ honest_halt)
poll ∈ agent_bus(wait)+poll_hint — ¬ pipeline(result) as primary
implement = seat=cursor-sdk Composer (omit model=)
¬ openai/gpt-5.5 dual-phase as primary
```

## Cascade

```
M0 Intake → M-Arch? → M-Densify → audit+approve → M-Implement → M-Verify
```

| Stage | Seat | Exit |
|---|---|---|
| **M0 Intake** | Cursor | Target file, SLOC, intended `target_package_dir`, consumer grep, `git rev-parse HEAD` |
| **M-Arch** | `cdp/opus-5` (default) · `cdp/fable` when outside check / Opus-unsure | Architecture verdict sidecar: package cuts, `__init__` public surface, consumer graph |
| **M-Densify** | `cursor/grok-4.6` (`seat=cursor-sdk`, `contract=light-bounded`) | `tmp/modularize-plans/{name}.md` — MODULES + IMPLEMENTATION GUIDE + `files_expected` |
| **M-Implement** | `seat=cursor-sdk` Composer (`contract=implement` \| `pure-mechanical`) | Package dir + modules + re-exports + consumer updates |
| **M-Verify** | Cursor | `compileall` · `ruff` · `scripts/modularize scan` green on new package |

### Mode skip (M-Arch)

| Condition | M-Arch |
|---|---|
| Red (>400 SLOC) ∨ complex consumer graph | **FIRE** (default) |
| Yellow (301–400) ∧ trivial single-cut | SKIP with disposition why (Mode A analog) |
| Flow B (plan exists) / Flow C (state resume) | SKIP arch; densify/implement per flow |

Publish a stage disposition table before densify when M-Arch could fire.

### Admit-proof (M-Arch — BINDING)

Same turn as M-Arch disposition claim:

1. **Admit:** `team_dispatch(model=cdp/opus-5|cdp/fable, …)` returns `execution_id` + `poll_hint` (quote), **or** warm `cse_session(op=followup)` into live operator-proxy CSE per `cdp-operator-proxy` / seed Mode B transport — **or**
2. **Honest halt** naming the blocker.

Forbidden: announce-only “will fire CDP.” Poll/harvest may continue later; admit is same-turn.

## Skill delivery floor (BINDING — fail closed)

| Leg | Delivery |
|---|---|
| **M-Arch (CDP)** | **Inline** into sealed prompt / `<invariants>`: `architecture-invariants` + `modularize-discipline` + `ulg-architecture` `[ulg:*]` when splitting ULG `services/`/`libs/`. Prefer Customize attach for Claude-slug skills when available; **non-slugs must be inlined**. Cortex stage = backup only. **Halt** if floor missing. |
| **M-Densify** | Same `<invariants>` + M-Arch verdict URI in densify packet |
| **M-Implement** | Densified plan + invariants; mechanical fidelity to package map |

URI-cite alone ≠ delivery. Matches `claude-ai-cdp-navigation` annex modularize row.

## Flows

| Flow | Trigger | Path |
|---|---|---|
| **A — Full** | source file path | M0 → M-Arch? → densify → approve → implement → verify |
| **B — Implement only** | `tmp/modularize-plans/{name}.md` | Skip to M-Implement → verify |
| **C — Resume** | `tmp/modularize-plans/{name}-state.md` | Resume M-Implement with `<prior_pass>` → verify |

Detection order: `-state.md` → C; under `tmp/modularize-plans/` `.md` → B; else → A.

## M0 — Intake

```bash
source ~/.venvs/universal/bin/activate
python scripts/modularize scan {path}
git rev-parse HEAD
rg "from {module_dotted_path}|import {module_dotted_path}" --type py
```

Propose `target_package_dir` (package-shadow: `foo.py` → `foo/`). Read source; compose
`<invariants>` (skill floor + `.cursor/invariants/modularize.md` redirect + domain rules)
and scoped `<architecture>` replacement table (only patterns present in source — see
command/legacy table: logging→universal_logging, httpx→transport_utils, etc.).

Write packet: `tmp/modularize-plans/{sanitized_name}-packet.md` (six-block handoff shape).

## M-Arch — CDP bind

Packet emphasizes: **file → package directory**; bind module cuts + public surface.
Transport: `team_dispatch(model=cdp/opus-5, …)` (or fable). Poll:

```python
agent_bus(tool="wait", arguments=poll_hint.arguments_json)  # re-call; wait_seconds≤60
```

¬ `pipeline(op=result)` as primary for CDP. Harvest verdict to
`cortex://notes/system/threads/…` or `tmp/modularize-plans/{name}-arch.md`; index
`arch_uri` + `arch_execution_id` in state.

Cursor audits arch answer (package-shadow present? forbidden names? public surface?).

## M-Densify — Grok

```python
team_dispatch(
  op="generate",
  seat="cursor-sdk",
  model="cursor/grok-4.6",
  contract="light-bounded",
  packet_path="tmp/modularize-plans/{name}-packet.md",  # or prompt with plan task_guidance
  dispatch_thread_id=f"modularize-{name}-densify",
)
# poll poll_hint via agent_bus(wait)
```

Task: produce MODULES (under `target_package_dir`) + IMPLEMENTATION GUIDE +
DEDUPLICATION OPPORTUNITIES + `files_expected`. Read source/consumers live via tools.
Inherit arch verdict + invariants — ¬ invent cuts that violate floor.

Write plan to `tmp/modularize-plans/{name}.md`.

### Structural audit (pre-approve)

1. Forbidden names (`utils.py`, `helpers.py`, `common.py`, `misc.py`, vague `base.py`)
2. Package-shadow layout (not sibling-prefix at parent)
3. No direct `httpx` / raw sockets; `universal_logging` only
4. `__init__` does not re-export `_` internals unless consumer requires

On fail: re-densify with `<prior_pass>` or surface. On pass: present summary; **ask
operator approval** before M-Implement (Flow A).

## M-Implement — Composer

State file `tmp/modularize-plans/{name}-state.md`:

```yaml
source: {path}
target_package_dir: {dir}
git_sha: {sha}
packet: tmp/modularize-plans/{name}-packet.md
plan: tmp/modularize-plans/{name}.md
arch_uri: {uri or null}
arch_execution_id: {id or null}
phase: 2  # densify=1-ish; implement=2; done
last_completed_step: 0
files_pending: [...]
files_written: []
execution_ids: []
errors: []
```

```python
team_dispatch(
  op="generate",
  seat="cursor-sdk",
  # omit model= → Composer
  contract="implement",  # or pure-mechanical when plan fully pinned
  packet_path="tmp/modularize-plans/{name}-implement.md",
  nest_under="{parent}" if cursor_sdk_gate held,
)
# poll agent_bus(wait) + poll_hint — ¬ pipeline(result)
```

Execute IMPLEMENTATION GUIDE: create package, write modules, `__init__` re-exports,
update consumers, delete original file only after `__init__` verified. End with
Files written summary. Cross-check disk; update state; Flow C if partial.

## M-Verify

```bash
"$HOME/.venvs/universal/bin/python" -m compileall -q {package_dir} {consumers}
"$HOME/.venvs/universal/bin/ruff" check {written} {consumers}
./scripts/modularize scan {package_dir}
```

Documentation contract audit (events / API / public import surface) — record in closeout.
Report: paths + SLOC, cascade seats (cdp/opus → grok densify → Composer), state path.

## Escape hatches

| Escape | When |
|---|---|
| `scripts/modularize plan {file}` | CDP/Stargate load blocks; quick sketch only — labeled escape |
| `web-claude` / multi-session bus | Very large splits needing persistent thread (opt-in) |

¬ local in-seat implement as default for red files. ¬ `openai/gpt-5.5` E2E as primary.

## Anti-patterns

| Bad | Good |
|---|---|
| gpt-5.5 owns plan+execute | CDP arch → Grok densify → Composer |
| `pipeline(result)` for CDP/sdk | `agent_bus(wait)` + `poll_hint` |
| URI-only architecture skills on M-Arch | Inline skill floor; fail closed |
| Sibling `foo_a.py` beside `foo.py` | `foo/` package-shadow |
| Grok densify+implement same leg | Split densify / implement |
| Announce CDP without admit | Quote execution_id + poll_hint or halt |
| Skip operator approve after densify (Flow A) | Ask before Composer |

## Relation to `/layer`

Same cascade *shape* as work-item-seed → layer (arch → densify → compose). Deliverable
differs: **package directory** vs closable todo. `/layer` G1 must also carry the
architecture skill floor before densify (`abstraction-layering` § G1 skill delivery).
