---
name: closeout-triage
description: Decide green vs red from closeout_read verdict_line and status_line only.
---

## Trigger
Load on `DONE|` or `CLOSEOUT|` before `bus_send` or `steer_conductor`.

## Refuse
Do not read diffs, full files, or a second sidecar.

## Steps
1. Call `closeout_read` once.
2. Green → `REQUEST|review` with head sha from score state.
3. Red with live conductor in score → `steer_conductor` quoting failure lines.
4. Red without conductor → `commission` fix packet with same work_key and ACs.

## Falsifier
Second `closeout_read` or a file read outside tools.
