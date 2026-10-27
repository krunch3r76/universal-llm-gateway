---
name: packet-authoring
description: Render a lane-B commission packet from schema fields only (scope, files_expected, acceptance).
---

## Trigger
Load when `commission` needs a new child packet and no pre-rendered packet_path exists.

## Refuse
Do not paste closeout bodies, bus turn prose, or sidecar text into the packet.

## Steps
1. List `files_expected` and numbered acceptance criteria from the tool inputs only.
2. Add the fixed constraints block (no land/push/manage/git/hop; composer-2.5 constant).
3. Call `commission` with those fields; let the tool render and hash the prompt.

## Falsifier
Packet contains a sentence copied from a closeout or bus body.
