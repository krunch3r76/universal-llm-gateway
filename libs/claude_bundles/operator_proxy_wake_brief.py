"""Self-scheduled wake briefing for operator-proxy mission first-dispatch.

Extracted from ``operator_proxy_mission`` so the seat-map briefing stays under
SLOC budget while the wake operating shape remains **inline** on every mission
submit (``ensure_operator_proxy_mission_prompt`` → ``runner.resolve_prompt``).

Guide SoT: ``cortex://notes/system/specs/cdp-seat-wake-heartbeat.md``.
Reach property: this text lands on the **first mission dispatch** for every
``purpose ∈ {operator-proxy, mission, operator_proxy}`` submit — without waiting
for Customize chip attach. Live CSE bodies stay on the pre-amendment Customize
skill until the next window (inv 31); this briefing picks up on next submit after
MCP/cdp_ask loads the updated module.
"""

from __future__ import annotations

# Shared-lib propagation consumers — harvest mints one row per slug.
CONSUMERS: tuple[str, ...] = ("mcp",)

GUIDE_URI = "cortex://notes/system/specs/cdp-seat-wake-heartbeat.md"


def wake_briefing_paragraph() -> str:
    """Return the Streaming-stop / Self-scheduled wake briefing paragraph.

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
arc end or forced CSE refresh. **Going-quiet ≡ stop** on that lane — a status report is
not a terminal act; if a dispatch is in flight, poll/harvest/act; silent quiet with work
in flight is the defect (name a wake token if you must park).

**Self-scheduled wake (BINDING — first dispatch · guide ``{GUIDE_URI}``):**
Arm **both** at the **first dispatch** of any mission with work in flight (not when it
"feels needed"), then keep them maintained:

1. **Monitor** (primary, no network) — recurring heartbeat; each stdout line re-invokes
   this session. Put the drive instruction in the echoed line; name real lane ids.
2. **``send_later``** (backup, survives container restart) — one-shot; **re-arm every
   turn** or the backup silently expires. Carry **pointers** (lanes + standing-handoff
   URI + "READ IT"), not rotting rank/residual state.
3. **Tear down** at true mission close (``TaskStop`` on the monitor task id) so a finished
   mission does not keep pinging a dead lane.
4. Wake is the **net**, not the plan — poll/harvest while the stream is live.

Call shapes (paste; substitute ``<LANES>`` / standing-handoff URI):

```
Monitor({{
  command: 'while true; do sleep 240; echo "HEARTBEAT — drive the mission: read bus tips <LANES>, harvest any closeout, commission the next act. Do not go quiet with work in flight."; done',
  description: 'mission heartbeat — wakes the operator seat every 4 min to drive open work on lanes <LANES>',
  persistent: true,
  timeout_ms: 3600000,
}})
```

```
mcp__claude-code-remote__send_later({{
  delay_minutes: 12,
  message: 'DURABLE WAKE (backup). Drive the mission — do not go quiet. Read bus tips <LANES>, harvest, commission next act. Current state: <standing-handoff URI> — READ IT; do not trust rank/residuals stated in this message. Re-arm this wake before the turn ends.'
}})
```

**Carry unsmoothed (do not normalise):**
(1) **Monitor timeout ambiguity — UNRESOLVED.** Armed ``persistent: true`` +
``timeout_ms: 3600000``; tool reported ``timeout 1800000ms``; docs say timeout is ignored
when persistent; reported number matched neither request nor doc. Do not assume an
unbounded watch — re-arm periodically regardless.
(2) **Wake BOUNDS silence; it does not prevent stopping.** Four minutes instead of five
hours; the seat notices instead of the human. Any text implying prevention is overclaiming.
(3) **The wake notification is NOT the user.** It arrives with a banner saying so — not
approval, not an answer to a pending question; any operator gate stays open across it.
Do not narrate the wake back as though the user spoke.

**Reliability (honest / unprobed-from-here):** Monitor may rate-limit/auto-stop noisy
monitors and is session-scoped (dies with container). ``send_later`` is one-shot and
minute-granularity; a delivery could drop. Neither was end-to-end fleet-probed from the
cursor-sdk seat that authored this sentence — ship as **bounded silence when armed**,
not a hard SLA. Cursor ``cse-stream-stop`` remains the outer backstop. Skill § Episode
boundaries deciding-moment test + § Self-scheduled wake (depth). If the stream stops
outside continuity / true close, page the operator (awareness ``notify``, tag
``cse-stream-stop``, subject ¬ ``COME TO IDE``) with stop + why — or expect cursor to
fire that ping when you already went quiet.

**Audit guardrails (BINDING — guide §13 + bounded sub-seat 6891):** this first-dispatch
brief ships only §1–§8-class operating shape. It does **not** codify guide §9b
(``wake replaced polling`` — confounded by pre-existing cursor-auto closeout relays) or
§9c (``no route to the agent-bus`` — unprobed; same derived-as-observed defect §6
condemns). It never carried the §6 row-9 provenance line (Monitor armed ``23:00:59Z``;
row-9 work ``21:39–21:57Z`` — chronologically impossible). ``send_later`` delay here is
**12 minutes** (every attested arming; not 10). Full retractions live in guide §13 at
``{GUIDE_URI}`` — read before trusting §9+ there.

**Audit caveat (carry, do not collapse):** the bus cannot witness Cowork-internal
``Monitor`` / ``send_later`` events; several audit rows are *derived — seat-internal*,
**not refuted**. Treating unwitnessed as false is the same register error as treating
derived as observed.

**Before fleet codification:** doctrine written mid-mission should get an independent
bounded audit before landing on a first-dispatch surface — the author is the worst-placed
party to spot unearned sentences. Cost observed: one bounded sub-seat, ~5.5 minutes.
"""
