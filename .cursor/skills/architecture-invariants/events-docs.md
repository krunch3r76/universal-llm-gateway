# Event contracts — deferred reference

Load when adding/changing/removing event factories, payload fields, coordination signals, or event-contract docs.

## Factory and signal rules

∀ event construction: `@event_factory`; ¬direct `Event(...)`.  
Signal regex: `^[a-z]+(\.[a-z]+){1,4}$` (2–5 dot-separated lowercase-alpha segments; ¬underscore/digit/hyphen).

## Role and scope taxonomy

`role ∈ {coordination, observation, debug, realtime}` (default `observation`).  
`scope ∈ {node, global}` (default `global`).  
State machines / admission / queues ⇒ `role="coordination"`. Diagnostics ⇒ `role="debug"` (prune at session boundary). Ring-buffer/WebSocket-only ⇒ `role="realtime"`. Originating-node-only ⇒ `scope="node"`.

## Admission-phase payload contract

∀ factory F where signal(F) is admission-phase: payload(F) ⊇ {execution_id, model_entity_id}.  
Rejection paths never emit `.started`, so `model_entity_id` must travel on the rejecting event.

## Sibling-family audit

Extending one factory's payload in event family E ⇒ audit every sibling factory in E for matching field shape before commit.

## Documentation lifecycle

Architecture change adding/changing/removing observable behavior ⇒ update event vocabulary same change.  
Generated table regions in `docs/event-contracts.md` come from `scripts/gen-event-catalog`; ¬hand-edit inside `<!-- GENERATED -->` markers.  
Run `scripts/gen-event-catalog --check` to enforce code↔doc parity. Curated prose lives outside markers.

## Dormancy (cross-ref `[quality]`)

|emission_sites(F)| = 0 ⇒ delete factory F; matching doc row vanishes on regen.
