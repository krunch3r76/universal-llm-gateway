---
name: event-instrumentation-discipline
description: "On ULG code touching behavioral edges or @event_factory emit sites — decide observation events; spot prune/relabel/sample on hot signals. Code-work floor with architecture+docstring skills."
---

# Event Instrumentation Discipline

## Invariant

```
∀ ULG behavioral edge authored ∨ patched:
  (∃ reason_to_log(edge) ⇒ consider @event_factory observation event) ≺ done_claim
∧ (touch(existing_emit_site ∨ hot_signal) ⇒ consider prune|sample|relabel|retire)
∧ ¬ grow_volume(already-hot signal) without justification
```

Events are ULG's **structured log replacement** — "report what's happening,
where." This is **write-time discipline** carried by the implementing seat, like
docstrings. It is **not** a path-sim Gate-2 harvest table, **not** a CDP R
challenge, **not** a footer nudge.

ULG code-work floor (with `architecture-invariants`, `ulg-architecture`,
`docstring-quality`): load before first write or implement dispatch. Empty
`required_skills` does not waive — the floor default-loads the set.

## The two decisions (bidirectional)

| While writing… | Ask | Default |
|---|---|---|
| A behavioral edge you would otherwise **log** (decision/branch, failure vs retry, lifecycle transition, recovery, handoff) | Is this worth an **observation** event? | Add when a future debugger/operator would want "what happened, where" — else plain log |
| Near an **existing emit site** or a **hot signal** on the touched surface | Is it still earning keep? | `sample` / `relabel` / `retire` when high-frequency + low diagnostic value; leave correctness-load-bearing signals |

**Log→event heuristic:** if there is a reason to log, there may be a reason to
encapsulate it as an event. Not every log becomes an event — high-frequency inner
loops stay logs (or sampled). The bar is *diagnostic value to a future
reader*, weighed against **event-server load**.

**Load awareness (BINDING).** The event server is capacity-constrained; a handful
of hyper-frequent signals dominate volume. **Do not add an observation event on an
already-hot path**, and do not multiply near-duplicate signals. Adding a
low-frequency event is cheap; adding one inside a hot loop is not.

## Role taxonomy (from `docs/event-contracts.md`)

| `role` | Meaning | Instrumentation stance |
|---|---|---|
| `coordination` | Consumed by state machines / admission / queues — suppressing breaks correctness | Only when the edge genuinely feeds a consumer; **do not** tag telemetry `coordination` |
| `observation` | Debugging / monitoring — safe to suppress, dedupe, scope to node | Default for log→event opportunities |
| `debug` | Temporary diagnostic; pruned at session boundary | Short-lived instrumentation only |
| `realtime` | High-frequency ephemeral (ring buffer, not SQLite) | High-rate streams that must not hit persistence |

**Anti-pattern:** labeling a high-frequency telemetry signal `coordination`
(inflates the correctness-load-bearing class and defeats pruning). Pick the role
by who consumes it, not by importance-feel.

## Mechanics

- Emit via the shared `@event_factory` decorator (see existing emit sites, e.g.
  `services/universal-stargate/systems/pipeline/core/events/`,
  `libs/cortex_store/events_imprint.py`). Set `role` and `scope` deliberately.
- Catalog / dedup: `scripts/gen-event-catalog {generate|check}` enumerates declared
  signals — check before minting a new signal name (avoid near-duplicates).
- New/renamed `@event_factory` signal ⇒ the `docs/event-contracts.md` GENERATED
  region is regenerated via `scripts/gen-event-catalog`, never hand-edited
  (`docs-write-guard_ws`).

## Cited failure path (binding)

```
∀ named_path P cited as the reason to add an observation event ∨ falsifier:
  verify(P exists in live call graph) ≺ emit_or_instrument(P)
¬ instrument(doctrine_as_written) when P does not exist
```

An event on a path that never runs reads clean forever (slogan without a
falsifier). Doctrine/spec names are hypotheses until the call site is read.
Taught instance: `a:28973` — cited warm-submit inheritance did not exist;
instrument followup **resolution**, not submit.

## Ship consideration (not a hard scan gate)

Unlike docstrings, there is **no criticals=0 event scan** — add/prune is a
judgment, not a conformance count. Closeout obligation:

```
∀ implement close whose files_expected touch @event_factory / behavioral edges:
  state in closeout — events added (signal · role · why) OR "no event warranted (reason)"
  + any prune/relabel candidates spotted on the touched surface
```

One line is enough. Silence on an event-bearing change is the miss.

## Second pass (not first)

`/overhaul` is the **residual** whole-subsystem sweep (noise-profile keyed) for
add/prune the narrow first-pass edit could not see —
`todo:overhaul-event-opportunity-noise-profile`. It catches what first-pass
instrumentation missed; it is not where first-pass mindfulness lives. Server-load
audit of hot dominators: `todo:event-server-top-signal-prune`.

## Dispatch / packet (delivery per seat)

| Seat | Delivery |
|---|---|
| Cursor / cursor-sdk (`cursor/*`) | `Use the event-instrumentation-discipline skill` — self-fetch; add to `skills=[…]` on `contract=implement` dispatches |
| web-anthropic / life | skill-inline excerpt (slug alone fails off-cursor) ∨ Customize Skills (`shared_sync`) |

Cite this slug with the architecture + docstring floor on ULG code handoffs.

## Related

`architecture-invariants` · `ulg-architecture` · `docstring-quality` (parallel
write-time floor bar) · `implement-todo` §5 (floor load + closeout) · `path-sim`
Stage-B (`skills=`) · `path-sim` § Event instrumentation in review (R-after
challenge on **`cdp/opus-5` `purpose=review`**) · `/work-item-review` (default-on after path-sim
Stage-B · Opus review substrate) · `debug-with-events` (query technique) ·
`docs/event-contracts.md` (role/scope taxonomy) · `scripts/gen-event-catalog`.
