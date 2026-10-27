---
name: house-heads
description: Name the current git head sha for DONE and review bus lines.
---

## Trigger
Load when posting `DONE|`, `REQUEST|review|`, or review lines that must cite head sha.

## Refuse
Do not invent a sha; do not run git from the agent.

## Steps
1. Read `state` in score.md for the recorded head.
2. If missing, use the head named in the triggering turn body.
3. Include that sha in the one-line bus body.

## Falsifier
DONE or REQUEST line cites a sha not present in score or the turn.
