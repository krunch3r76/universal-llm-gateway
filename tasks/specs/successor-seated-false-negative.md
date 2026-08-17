# Spec — successor_seated must reflect registration, not harvest abort

**Slug:** `successor-seated-false-negative`  
**Kind:** investigate+fix  
**Todo:** `todo:successor-seated-false-negative`  
**Arc:** 9031 / successor-seated-false-negative  
**Recon:** `cortex://notes/system/recon/successor-seated-false-negative/s2-anchors.md` (`written_sha256` `99a8753306bbaa347ce772bfe7187ce1a12780b2ee4b68c2fc5bb94fcefa47db`)  
**Evidence:** `cortex://notes/system/threads/9031-successor-seated-false-negative-evidence.md`  
**Cortex spec:** `cortex://notes/system/specs/successor-seated-false-negative.md`  
**S6 entry:** G1  
**Post-seed:** Use the `abstraction-layering` skill at G1  
**Tree read at seed:** `91d4166896cc646060c82d8a7a7cfcadf3cb87aa`

## Problem

The hop harvest-failed terminal emits `successor_seated: false` whenever a `successor_birth_id` is present. That assignment is unconditional (`hop_harvest_terminal.py:78`). It does not read the cdp-registry projection the same hop already posted as `TYPE: SEAT_REGISTRATION`.

Live 2026-08-17, thread 9031: turn 60 @13:53:36Z stamped registration `4c82db64…` for birth `b8aec845…`; turn 62 @13:57:54Z reported `successor_seated: false` for the same birth after generate harvest `aborted`. The successor was live (turn 63). Seats treat the field as seating truth; no production Python consumer gates predecessor stand-down on it (severity = reporting/protocol, not control-plane).

Related but not this work: `todo:hop-terminal-vs-successor-liveness` v1 bound hop *status tokens* to generate harvest and **deferred** CSE liveness. This item is that deferred seating boolean on the **fail** path, after registration was already observed.

## Design bind (in-seat; Mode B skipped)

**Question:** what should `successor_seated` on a hop harvest terminal claim?

**Bind:** it is a claim about whether this hop's successor is already observed as registered (watch-row `registration_id` after confirm, i.e. the same fact `SEAT_REGISTRATION` projects), **not** about generate harvest success/abort, and **not** about empty `chat_url`.

| State | Honest `successor_seated` |
|---|---|
| Harvest aborted, watch/registry already has successor `registration_id` for this `successor_birth_id` | `true` |
| Harvest aborted, no registration observed yet | `false` (birth remains a minted key) |
| Armed admit (generate still open) | may stay `false` — seating not yet claimed |
| Harvest ok | **not this todo** — positive discriminator stays deferred (`hop-terminal-vs-successor-liveness-success-discriminator-deferred.md`) |

Rejected: keep hardcoded `False` because "birth is not seating" (true of birth_id; false of a field named `successor_seated` once registration exists). Rejected: wire predecessor stand-down to this boolean in v1 (no current Python consumer; stand-down is `SEAT_STAND_DOWN_ACK`). Rejected: treat empty `chat_url` as unseated (`body.py` — descriptive, not I6).

## Scope

- `build_harvest_failed_turn` / `post_harvest_terminal_for_action` (`hop_harvest_terminal.py`)
- Caller that has the watch row at revoke time (`hop_cadence_stall_reconcile` harvest terminal post)
- Regression: harvest aborted + registration present ⇒ `successor_seated is True`
- Flip or split `test_harvest_failed_turn_shape_quotes_status_failed` so it no longer requires False when registration is present

## Out of scope

- Id-space exclusion / self-supersede capture (HEAD `91d41668` / 9435 `49a7d130`)
- Why one seat yields two execution ids
- a:29299 repair 2 read-side lease probe (`todo:operator-plane-write-authority`)
- Harvest_ok positive `registration_id` discriminator (deferred file on the liveness todo)
- Wiring stand-down ACK to this field
- Amending already-posted harvest terminals (history-integrity append-only stands)

## Acceptance

1. Failing-first test: given `action="revoked"` and a watch row that already carries `successor_birth_id` **and** successor `registration_id`, the harvest-failed payload has `successor_seated is True`. On pre-fix HEAD this test fails because `:78` assigns False.
2. Harvest-failed with birth but **no** registration still emits `successor_seated is False` (armed/unobserved seating).
3. Empty `chat_url` does not flip the boolean.
4. No new production reader is required for v1 (reporting honesty). Do not silently add a stand-down gate.
5. Spec/todo updated; related_to `todo:hop-terminal-vs-successor-liveness` without widening that todo's v1 ACs.

## Loci

- `services/git_integration_worker/cursor_auto/hop_harvest_terminal.py` (`build_harvest_failed_turn`:75-78, `post_harvest_terminal_for_action`:112-148)
- `services/git_integration_worker/cursor_auto/hop_cadence_stall_reconcile.py` (confirm stamp `:663-670`; harvest terminal post on revoke)
- `services/git_integration_worker/tests/test_hop_terminal_harvest_honesty.py:133-151`
- `services/git_integration_worker/cursor_auto/continuity_hop.py:334-337` (armed False — leave unless G1 densify says otherwise)
