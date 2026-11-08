"""Hold a Glass wake while its target agent is mid-turn, or when its workspace is unresolved.

A wake submitted into a running composer does not queue behind the turn. The
wake must wait until the tab is idle. Composer services must come from the
agent's own workspace instance: the window-root services belong to the
``empty-window`` instance, and a submit there loads a second copy of the
conversation and runs it where the operator cannot see it (friction:43076).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

AGENT_RUNNING = "agent_running"
WORKSPACE_UNRESOLVED = "workspace_unresolved"

AGENT_RUN_STATE_FN = r"""
async function agentRunState(rootMap, agentId) {
  const r = resolveWorkspaceServices(rootMap, agentId);
  if (r.targetEnv && !r.instanceMatched) return {ok: false, reason: 'workspace_unresolved'};
  const cds = r.services.get('composerDataService');
  if (!cds) return {ok: true, running: false, status: ''};
  let h = null;
  try { if (typeof cds.getHandleIfLoaded === 'function') h = cds.getHandleIfLoaded(agentId); } catch (e) {}
  if (!h) { try { h = await cds.getComposerHandleById(agentId); } catch (e) {} }
  const d = (h && h.data) || {};
  const status = String(d.status || '');
  const bubbles = Array.isArray(d.generatingBubbleIds) ? d.generatingBubbleIds.length : 0;
  return {ok: true, running: status === 'generating' || bubbles > 0, status};
}
"""


def probe_agent_run_state_js(agent_id: str) -> str:
    """CDP expression: run state of one agent in its own workspace instance."""
    from cursor_bridge.glass_services_js import (
        GLASS_SERVICE_MAP_FN,
        RESOLVE_WORKSPACE_SERVICES_FN,
    )

    aid = json.dumps(str(agent_id or "").strip())
    return (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        f"{RESOLVE_WORKSPACE_SERVICES_FN}\n"
        f"{AGENT_RUN_STATE_FN}\n"
        f"  const agentId = {aid};\n"
        "  if (!agentId) return {ok: false, reason: 'no_agent_id'};\n"
        "  const anchor = document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"]')"
        ' || document.querySelector(\'[aria-label="Prompt"][contenteditable="true"]\');\n'
        "  if (!anchor) return {ok: false, reason: 'no_anchor'};\n"
        "  const byId = glassServiceMap(anchor);\n"
        "  if (!byId) return {ok: false, reason: 'no_services'};\n"
        "  return await agentRunState(byId, agentId);\n"
        "})()"
    )


async def probe_agent_run_state(ws_url: str, agent_id: str) -> dict[str, Any]:
    """Evaluate the run-state probe; a failed probe returns ``ok`` false, never raises.

    A failed probe does not hold the wake: the submit script repeats the same
    check before it calls the composer service.
    """
    import websockets

    expression = probe_agent_run_state_js(agent_id)
    request = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        },
    }
    try:
        async with asyncio.timeout(5.0):
            async with websockets.connect(ws_url, max_size=2_000_000) as ws:
                await ws.send(json.dumps(request))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == 1:
                        break
    except Exception as exc:  # noqa: BLE001 — probe is advisory; submit re-checks
        logger.warning("glass_agent_run_state: probe failed %s", type(exc).__name__)
        return {"ok": False, "reason": "probe_failed"}
    value = ((msg.get("result") or {}).get("result") or {}).get("value")
    return value if isinstance(value, dict) else {"ok": False, "reason": "probe_failed"}


def run_state_hold_reason(state: dict[str, Any]) -> str | None:
    """Refusal reason that holds the wake, or ``None`` when delivery may proceed."""
    if state.get("ok") is False and state.get("reason") == WORKSPACE_UNRESOLVED:
        return WORKSPACE_UNRESOLVED
    if state.get("ok") and state.get("running"):
        return AGENT_RUNNING
    return None
