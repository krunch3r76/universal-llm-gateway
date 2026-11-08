"""Wake hold on a busy Glass agent and workspace-instance resolution (friction:43076)."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cursor_bridge import wake_delivery as wd
from cursor_bridge.chat_surface import classify_surface
from cursor_bridge.glass_services_js import (
    RESOLVE_WORKSPACE_SERVICES_FN,
    submit_wake_line_js,
)

pytestmark = pytest.mark.offline

_ULG = "/mnt/torus/projects/universal-llm-gateway"
_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _run_js(functions: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Execute resolver JS against a Glass shape witnessed on :8 (2026-11-08).

    The live ``workspaceCollectionService`` has ``getWorkspaceInstances`` and no
    ``getWorkspaceInstance``; the window-root services are the empty-window
    instance's.
    """
    script = f"""
const document = {{ querySelector: () => null, querySelectorAll: () => [] }};
{functions}
const spec = {json.dumps(spec)};
const handle = (status) => ({{ data: {{ status, generatingBubbleIds: [] }} }});
const cds = (status) => ({{
  getHandleIfLoaded: () => handle(status),
  getComposerHandleById: async () => handle(status),
}});
const entries = (pairs) => ({{ instantiationService: {{ _services: {{ _entries: new Map(pairs) }} }} }});
const rootChat = {{ name: 'empty-window' }};
const ulgChat = {{ name: 'ulg' }};
const rootCds = cds(spec.root_status);
const emptyInst = {{ workspaceIdentifier: {{ id: 'empty-window' }}, displayName: 'empty',
  ...entries([['composerChatService', rootChat], ['composerDataService', rootCds]]) }};
const ulgInst = {{ workspaceIdentifier: {{ id: 'd95d', uri: {{ scheme: 'file', path: '{_ULG}' }} }},
  displayName: 'ulg',
  ...entries([['composerChatService', ulgChat], ['composerDataService', cds(spec.ulg_status)]]) }};
const wcs = {{ getWorkspaceInstances: () => spec.ulg_materialized ? [emptyInst, ulgInst] : [emptyInst] }};
const repo = {{ getAgentHeader: () => ({{ environment: {{ id: 'd95d',
  uri: {{ path: '{_ULG}', fsPath: '{_ULG}' }} }} }}) }};
const root = new Map([['agentRepositoryService', repo], ['workspaceCollectionService', wcs],
  ['composerChatService', rootChat], ['composerDataService', rootCds]]);
(async () => {{
  const r = resolveWorkspaceServices(root, 'agent-1');
  const run = typeof agentRunState === 'function' ? await agentRunState(root, 'agent-1') : null;
  process.stdout.write(JSON.stringify({{
    chat: r.services.get('composerChatService').name, matched: r.instanceMatched, run }}));
}})();
"""
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=20, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@_needs_node
def test_resolver_picks_agent_workspace_instance_not_window_root() -> None:
    out = _run_js(
        RESOLVE_WORKSPACE_SERVICES_FN,
        {
            "root_status": "completed",
            "ulg_status": "completed",
            "ulg_materialized": True,
        },
    )
    assert out["chat"] == "ulg"
    assert out["matched"] is True


@_needs_node
def test_run_state_reads_busy_handle_in_agent_workspace() -> None:
    from cursor_bridge.glass_agent_run_state import AGENT_RUN_STATE_FN

    out = _run_js(
        RESOLVE_WORKSPACE_SERVICES_FN + AGENT_RUN_STATE_FN,
        {
            "root_status": "completed",
            "ulg_status": "generating",
            "ulg_materialized": True,
        },
    )
    assert out["run"] == {"ok": True, "running": True, "status": "generating"}


@_needs_node
def test_run_state_refuses_when_agent_workspace_not_materialized() -> None:
    from cursor_bridge.glass_agent_run_state import AGENT_RUN_STATE_FN

    out = _run_js(
        RESOLVE_WORKSPACE_SERVICES_FN + AGENT_RUN_STATE_FN,
        {
            "root_status": "completed",
            "ulg_status": "completed",
            "ulg_materialized": False,
        },
    )
    assert out["chat"] == "empty-window"
    assert out["run"] == {"ok": False, "reason": "workspace_unresolved"}


def test_submit_script_gates_on_run_state_before_any_submit() -> None:
    js = submit_wake_line_js("BRIDGE_WAKE thread=1 turn=2", "agent-1")
    gate = js.index("await agentRunState(byId, agentId)")
    assert gate < js.index("await resolveComposerForAgent(byId, agentId)")
    assert gate < js.index("submitChatMaybeAbortCurrent(")
    assert "'agent_running'" in js


def test_run_state_hold_reason_table() -> None:
    from cursor_bridge.glass_agent_run_state import run_state_hold_reason

    assert run_state_hold_reason({"ok": True, "running": True}) == "agent_running"
    assert run_state_hold_reason({"ok": True, "running": False}) is None
    assert (
        run_state_hold_reason({"ok": False, "reason": "workspace_unresolved"})
        == "workspace_unresolved"
    )
    assert run_state_hold_reason({"ok": False, "reason": "probe_failed"}) is None


def test_agent_running_is_held_pending_not_refused() -> None:
    from bus_watch.endpoint_wake_transport import classify_display

    body = {"ok": False, "reason": "agent_running", "pasted": False, "delivered": False}
    assert classify_display(200, body, None) == ("pending", "agent_running")


@pytest.mark.asyncio
async def test_deliver_wake_holds_busy_cursor_agent_before_select_or_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = classify_surface(
        [], [{"text": "other chat", "selected": True, "agent_id": "other"}]
    )
    monkeypatch.setattr(
        wd,
        "list_pages",
        lambda _origin: [
            {"title": "Cursor Agents", "web_socket_debugger_url": "ws://x"}
        ],
    )
    monkeypatch.setattr(wd, "probe_surface", AsyncMock(return_value=surface))
    monkeypatch.setattr(
        wd,
        "probe_agent_run_state",
        AsyncMock(return_value={"ok": True, "running": True, "status": "generating"}),
    )
    select = AsyncMock(return_value={"clicked": True})
    paste = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(wd, "select_sidebar_target", select)
    monkeypatch.setattr(wd, "_paste_cursor_services", paste)

    out = await wd.deliver_wake(
        display="8", app="cursor", thread="22480", agent_id="agent-1", turn=103
    )

    assert out["reason"] == "agent_running"
    assert out["pasted"] is False
    select.assert_not_awaited()
    paste.assert_not_awaited()
