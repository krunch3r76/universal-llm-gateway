---
name: jupiter-dgpu-display-blank
description: ">-"
trigger_match_terms: ["jupiter-dgpu-display-blank", "jupiter_dgpu_display_blank"]
generator_version: "1.0.0"
---

# Jupiter dGPU display blank / restore

`operator asks blank|restore Jupiter NVIDIA head ∨ INSUFFICIENT_VRAM ∧ cosmic-comp holds dGPU ⇒ Use this skill`.

## Invariant

`∀ blank|restore: live NVIDIA head is DP-2 (U2868) ∧ ¬ disable Intel heads (DP-1 Cinema HD, HDMI-A-2 ASUS)`. The checked-in script still defaults to `HDMI-A-3`; that connector is disconnected.

Hybrid desk: Intel owns DP-1 (Cinema HD) and HDMI-A-2 (ASUS VG236). NVIDIA owns the U2868.

## Gap — disable does not release the 5090 reservation (2026-11-25)

Live NVIDIA head is `DP-2` (U2868), not `HDMI-A-3`. `HDMI-A-3` is disconnected. `DP-2` is the Xwayland primary: 3840×2160 @ 60 Hz, scale 1, position 2560,0.

With that head enabled, `nvidia-smi` reported `memory.used` 401 MiB and `memory.reserved` 497 MiB. `cosmic-comp` held 137 MiB (type G). Operator report: disabling the head leaves that NVIDIA reservation in place. `cosmic-randr disable` is not a VRAM reclaim. The 2026-07-16 drop (~1.4 GiB → ~15 MiB) was not reproduced this session. Do not blank the panel to chase the reservation while the desk is in use.

The script default `OUTPUT=HDMI-A-3` misses the live head. A blank that should target the 5090 has to disable `DP-2` only after `cosmic-randr xwayland --primary DP-1`.

## Gap — stub EDID / freeze (2026-11-26)

`∀ restore|mode on DP-2: analyze 4K readiness first`. Required before `cosmic-randr mode|enable|disable` on the NVIDIA head:

1. `wc -c /sys/class/drm/card1-DP-2/edid` — healthy U2868 was **256** bytes; **128** + product name `0x0000` = stub EDID (OSD power unlock / bad link).
2. `cosmic-randr list` must show **U2868** and a **3840x2160** mode. If the only current mode is **640x480**, do not apply 4K — `ModeNotFound` then repeated disable/enable **hangs cosmic-comp and freezes all heads** (operator had to SSH-restart the compositor).
3. Stub path: operator fixes DP input / auto / cable / monitor power until EDID is 256 and model is U2868; only then apply restore knobs. Prefer `timeout` on every `cosmic-randr` call. Never loop disable/enable against a stub.

OSD unlock (U2868PQU-class): **power off** → hold **Menu** → press **Power** → release Menu. Holding Menu while on does nothing.

## Procedure

From repo root:

```bash
scripts.local/jupiter-dgpu-display status
scripts.local/jupiter-dgpu-display blank
scripts.local/jupiter-dgpu-display restore
```

Override head: `scripts.local/jupiter-dgpu-display blank --output DP-2`

`∀ after blank|restore: quote nvidia-smi memory.used/free` (script prints BEFORE/AFTER). `silence ≠ success`.

## Restore defaults (U2868 last-known-good)

| Knob | Value |
|---|---|
| mode | 3840×2160 @ 60 Hz |
| scale | 1 |
| position | 2560,0 |
| xwayland primary | DP-2 |

Reapplied 2026-11-25 (`cosmic-randr mode` exit 0). Prior row (30 Hz, scale 1.25, position 4480,0) was the 2026-07-16 script default.

Env overrides: `OUTPUT`, `RESTORE_WIDTH`, `RESTORE_HEIGHT`, `RESTORE_REFRESH`, `RESTORE_SCALE`, `RESTORE_POS_X`, `RESTORE_POS_Y`, `JUPITER_HOST`.

## When to blank

`large Jupiter local load (e.g. Hermes-70) ∧ free VRAM short by compositor margin ⇒ blank before retry`. Idle models on other nodes are a separate eviction question.

`operator session still needs U2868 ⇒ ask before blank`; when operator already directed blank, keep blank until restore is requested.
