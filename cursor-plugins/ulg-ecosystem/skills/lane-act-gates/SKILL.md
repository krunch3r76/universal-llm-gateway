---
name: lane-act-gates
description: "Before firing or withholding a dispatch, asserting a lane is idle, or authoring or amending a packet — run that act's gate on this seat."
trigger_match_terms: ["fire a dispatch", "withhold a dispatch", "implement_ready", "assert a lane", "author a packet", "amend a packet", "NEXT_ADMIT", "CURSOR_SOURCE_REF_IN_FLIGHT"]
related_skills: ["cdp-operator-proxy", "retrieval-before-authoring"]
---

# Lane act gates

Per-act checks on this seat. Invariants and transport stay in `cdp-operator-proxy`.

## Trigger

About to fire a dispatch, withhold one, assert a lane's state, or author or amend a prompt, packet, or commission.

## Refuse

- Withhold `job=conductor` because `implement_ready` is unstamped.
- Assert idle, absent, or not-running without a read of that work thread on this turn.
- Compose a prompt, packet, or memorandum another model will act on.
- Edit another author's packet or acceptance criterion.
- Choose the act from a printed `NEXT_ADMIT` line, or from its absence.

## Steps

1. **Stamp vs contract.** `implement_ready` absent ⇒ withhold `job=implement` only. The same absence ⇒ fire `job=conductor`. Conductor G-rows write the stamp; `implement` consumes it.
   Falsifier: the withhold cites `implement_ready not stamped` as the reason a conductor was not fired.

2. **Read before a negative.** A withhold or "not running" is a claim about a lane. Read that work thread this turn (`agent_bus_read` `get`, `turn_number=latest`). Quote the turn time. An admit with no closeout on that thread means it is running. No work thread in hand means you have not shown the lane is empty — do not announce the negative.
   Falsifier: the running job is first learned from `409 CURSOR_SOURCE_REF_IN_FLIGHT` (`holder_dispatch_id`, `holder_thread_id`). Specimen: holder `dbcfa81246f2-ac9ffde6` on thread 13261, running since 00:55:37Z.

3. **Consequential text is a route.** A memorandum, prompt, or packet another model will act on goes to a cursor-sdk author via `team_dispatch` (`seat=cursor-sdk`). The commission names the target surface and requires `retrieval-before-authoring`. This seat does not draft the body.
   Falsifier: the text is a turn this seat composed, with no author dispatch and no named target surface. Specimen: thread 13259.

4. **Amendment is authoring.** Return a packet or acceptance criterion to its author. A count that changed because master moved is still an amendment.
   Falsifier: an acceptance criterion differs from the author's text and this seat made the diff. Specimen: thread 13257, expected commit count 2→3.

5. **Same structure, same act.** Steps 1–4 read the card's structure. A seed line `NEXT_ADMIT: …`, and a seed that omits it, are tool data. They do not select different acts for two cards of the same structure.
   Falsifier: two cards of the same structure received different acts in one sitting, and the only difference named is whether the seed printed `NEXT_ADMIT`.

## Falsifier

This body failed to bind if a later leg withholds a conductor for an unstamped `implement_ready`, announces a lane empty and then meets `409 CURSOR_SOURCE_REF_IN_FLIGHT`, composes or amends a packet on this seat, or treats `NEXT_ADMIT` text as the gate.
