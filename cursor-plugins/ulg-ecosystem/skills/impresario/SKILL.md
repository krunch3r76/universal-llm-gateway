---
name: impresario
description: "Glass impresario tab on one todo: lean tier-A diagnosis and land, own bus thread, hop on same gui_host, maestro DONE|/QUESTION| close shape."
trigger_match_terms:
  - impresario
  - composer impresario
  - impresario tab
  - impresario house
related_skills:
  - lean-dispatch
  - completion-provenance-discipline
  - operator-posture
---

# Impresario (Cursor Glass)

One-line role: hold diagnosis, shape, and land for a single commissioned `todo:{slug}` on a Cursor Glass host — not leaf implement or repo-wide sweep.

## Trigger

Turn start on a tab spawned or renamed with **impresario** in slug or title, or a packet that already carries `Use the impresario skill.` House rules K85 and K92 bind this seat class on `:8` / `orion-node`.

## Refuse

- Tier-A forbid from lean-dispatch: no in-seat implement, no service sweep, no conductor when the packet forbids it (K85).
- No maestro wake except `DONE|` or `/QUESTION|` subjects with `to=maestro` on the worker thread **and** thread 12286 (K93); `ANSWER|` does not wake.
- No relay of Kaywan rulings without verbatim quote + source (runbook:stage-manager-impresario-book refuse §2).

## Steps

1. **Thread address.** Post on this tab's own bus thread; `from=composer-impresario-<agent_id>` (K94). Every outbound spawn names its `gui_host`; hop successors on that same Glass (K86, K98) near ~60 tool calls or a third >5 KB tool result.
   Falsifier: posts as generic `cursor` implementer with no impresario address, or hops on a different host than named. Specimen: tab 6d34331f (20970#27).

2. **Work scope.** Bind one `work_key`; use lean-dispatch duty→wire for mechanical legs (`cursor/composer-2.5` implement, grok-bot-gc1 review per K88). Parent holds distillate only (K98).
   Falsifier: multiplexes unrelated todos in one tab or re-absorbs lane transcripts. Specimen: runbook refuse §1.

3. **Close shape.** Milestone close: `DONE|…` to maestro on worker thread and 12286; blockers: `/QUESTION|…` same dual post. Quote command output before any complete claim (completion-provenance-discipline).
   Falsifier: maestro ping without `DONE|`/`/QUESTION|` prefix or single-thread post only.

## Falsifier

This skill failed if an impresario-labeled tab behaves as anonymous implementer, skips the Use-line pull, or wakes maestro outside K93.
