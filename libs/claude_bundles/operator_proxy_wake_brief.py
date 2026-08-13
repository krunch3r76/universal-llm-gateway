"""Self-scheduled wake briefing for operator-proxy mission first-dispatch.

Extracted from ``operator_proxy_mission`` so the seat-map briefing stays under
SLOC budget while wake posture stays **inline** on every mission submit
(``ensure_operator_proxy_mission_prompt`` → ``runner.resolve_prompt``).

Guide SoT: ``cortex://notes/system/specs/cdp-seat-wake-heartbeat.md``.

**2026-08-07 bind (6661 / human principal):** durable keep-alive (Monitor loop +
re-arming ``send_later``) is **suspended** as a default. CDP one-off work from
the mission runner (``cdp/opus``) remains acceptable. Sole wake authority, when
re-enabled, is the mission **primary** orchestrator only — not hop successors
(``hop_cadence`` handoff body carries the hop-side prohibition).
"""

from __future__ import annotations

# CONSUMERS = import-nomination (GIW). INJECTORS = seat paste (cdp_ask).
CONSUMERS: tuple[str, ...] = ("git_integration_worker",)
INJECTORS: tuple[str, ...] = ("cdp_ask",)

GUIDE_URI = "cortex://notes/system/specs/cdp-seat-wake-heartbeat.md"


def wake_briefing_paragraph() -> str:
    """Return first-dispatch wake posture (keep-alive suspended by default).

    Intended to be embedded under the mission seat-map Streaming stop block.
    """
    return f"""\
**Streaming stop (BINDING — 2026-08-01 · inv 30):** ending this Cowork stream / turn is
authorized **only** for (1) continuity handoff to a new CSE (after launch confirmed) or
(2) true mission/episode close with ``TYPE: MISSION_CLOSEOUT`` + wake path + mission-debrief
notify. A **leg** DISPOSITION ("Mission leg complete", ratify one DIRECTIVE) does **not**
authorize stopping — keep the stream live; residuals stay in-mission as the next DIRECTIVE
or idle wait. Forbidden: "Nothing needs you" + stop while open residuals remain without
mission-close TYPE. **Persistent-lane carve-out (thread 6885):** on
``bus_lifecycle:persistent`` + long-lived operator, ``MISSION_CLOSEOUT`` only for true
arc end or forced CSE refresh.

**Self-scheduled wake / keep-alive (SUSPENDED — 2026-08-07 · guide ``{GUIDE_URI}``):**
Durable Monitor loops and re-arming ``send_later`` are **not** the default. The current
pattern produced fleet wake herds (hop successors each arming self-sustaining cycles
with no exit). **Do not arm Monitor. Do not re-arm ``send_later`` for keep-alive.**

| Allowed now | Forbidden by default |
|---|---|
| CDP one-off work from the mission runner (``cdp/opus``) | Arming Monitor / heartbeats "to stay alive" |
| Human-authorized wake on the **mission primary** only | Hop / child seats running durable wake cycles |
| One-shot ``send_later`` when a **named** in-flight job needs harvest (≤15 min, no re-arm forever) | Re-arm-every-turn durable wake with no termination |

**Sole-wake doctrine:** one monitor lane ↔ one mission (e.g. 6661 ↔ 6655). Only that
**primary** orchestrator may hold a wake cycle when keep-alive is re-enabled; subordinates
report up and go quiet. Tear down any inherited Monitor (``TaskStop`` on the task id this
seat armed). If clearing a pending self-wake, ``delete_trigger`` **only** recorded
``trigger_id``s — **never** class-delete (guide §16).

**Going-quiet with work in flight** remains a defect when a named job still needs harvest —
use a **one-shot** harvest wake or stay in the live stream; do not invent a standing
heartbeat fleet. Cursor ``cse-stream-stop`` remains the outer backstop for unauthorized
stops. Full historical arm recipes live in the guide — treat them as **parked**, not
first-dispatch defaults, until keep-alive is redesigned for primary-only.

**§7 GOOD/BAD — if/when a wake is armed, carry POINTERS, not STATE (guide §7):**
- GOOD (durable, cannot rot): lanes + standing-handoff cortex URI + "READ IT" + re-arm
  instruction. Trust the handoff for rank/residuals, not the wake body.
- BAD (rots between arming and firing): "next is row N" / named residual lists / ordinal
  rank. A twelve-minute-old message will be obeyed confidently after the world moved.
- If state must travel: stamp it and mark suspect (``state as of <ISO>, verify before acting``).
- Sharpening (guide §10): durable addresses only (cortex URIs, thread ids) — never
  ephemeral handles (monitor task ids, execution ids, dispatch ids).

**§8 RESOLVED — ``persistent: true`` does NOT make Monitor unbounded (guide §8):**
Observed historically: armed ``persistent: true`` + ``timeout_ms: 3600000``; tool reported
``timeout 1800000ms``; substrate later emitted ``[Monitor timed out — re-arm if needed.]``.
Effective lifetime ~30 min. Docs claiming timeout is ignored when persistent are wrong
or narrower than "no timeout." When keep-alive is re-enabled: (a) arm-once silently loses
coverage; (b) the timeout event is itself a wake — re-arm immediately; (c) ``send_later``
is not optional during the gap. Do not assume an unbounded watch.
"""
