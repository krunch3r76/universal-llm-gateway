---
name: jupiter-dgpu-display-blank
trigger_match_terms:
  - jupiter-dgpu-display-blank
  - blank U2868
  - blank HDMI-A-3
  - restore U2868
  - Jupiter compositor VRAM
  - cosmic-comp VRAM
  - INSUFFICIENT_VRAM display
description: >-
  On Jupiter dGPU VRAM pressure from the desktop — blank/restore the NVIDIA-attached
  head (default U2868/HDMI-A-3) before large local loads; measure VRAM after.
---

# Jupiter dGPU display blank / restore

`operator asks blank|restore Jupiter NVIDIA head ∨ INSUFFICIENT_VRAM ∧ cosmic-comp holds dGPU ⇒ Use this skill`.

## Invariant

`∀ blank|restore: default OUTPUT=HDMI-A-3 (U2868 on RTX 5090) ∧ ¬ disable Intel heads (DP-1 Cinema HD, HDMI-A-2 ASUS)`.

Hybrid desk: Intel owns two panels; NVIDIA owns U2868. Compositor tax on the 5090 is dominated by that head (~1.4 GiB observed 2026-07-16 → ~15 MiB after blank).

## Procedure

From repo root:

```bash
scripts.local/jupiter-dgpu-display status
scripts.local/jupiter-dgpu-display blank
scripts.local/jupiter-dgpu-display restore
```

Override head: `scripts.local/jupiter-dgpu-display blank --output HDMI-A-3`

`∀ after blank|restore: quote nvidia-smi memory.used/free` (script prints BEFORE/AFTER). `silence ≠ success`.

## Restore defaults (U2868 last-known-good)

| Knob | Value |
|---|---|
| mode | 3840×2160 @ 30 Hz |
| scale | 1.25 |
| position | 4480,0 |

Env overrides: `OUTPUT`, `RESTORE_WIDTH`, `RESTORE_HEIGHT`, `RESTORE_REFRESH`, `RESTORE_SCALE`, `RESTORE_POS_X`, `RESTORE_POS_Y`, `JUPITER_HOST`.

## When to blank

`large Jupiter local load (e.g. Hermes-70) ∧ free VRAM short by compositor margin ⇒ blank before retry`. Idle models on other nodes are a separate eviction question.

`operator session still needs U2868 ⇒ ask before blank`; when operator already directed blank, keep blank until restore is requested.
