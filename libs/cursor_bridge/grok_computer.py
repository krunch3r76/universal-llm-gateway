"""X11 idle reset and Grok Bot desktop.main CDP helpers for :9 / :9339."""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import json
import time
from typing import Any
from urllib.error import URLError

from cursor_bridge.cdp_pages import list_pages

_DEFAULT_AGENT_ID = "b5c63fe1-61b5-47d6-be6e-319ec999c383"
_GROK_WINDOW_TITLE = "Grok Bot"
_STORE_NOT_DURABLE = "store-not-durable"


def _normalize_display(display: str) -> str:
    raw = (display or ":9").strip()
    return raw if raw.startswith(":") else f":{raw}"


def reset_x11_screensaver(display: str = ":9") -> bool:
    """Reset X11 screensaver idle on ``display``; always closes the display handle."""
    lib_path = ctypes.util.find_library("X11")
    if not lib_path:
        return False
    x11 = ctypes.cdll.LoadLibrary(lib_path)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    dpy = x11.XOpenDisplay(_normalize_display(display).encode())
    if not dpy:
        return False
    try:
        x11.XResetScreenSaver(ctypes.c_void_p(dpy))
        x11.XFlush(ctypes.c_void_p(dpy))
        return True
    finally:
        x11.XCloseDisplay(ctypes.c_void_p(dpy))


def _cdp_origin(cdp_port: int) -> str:
    return f"http://127.0.0.1:{cdp_port}"


def _pick_grok_page(pages: list[dict[str, str]]) -> dict[str, str] | None:
    named = [
        p
        for p in pages
        if p.get("title") == _GROK_WINDOW_TITLE and p.get("web_socket_debugger_url")
    ]
    if len(named) == 1:
        return named[0]
    usable = [p for p in pages if p.get("web_socket_debugger_url")]
    return usable[0] if len(usable) == 1 else None


def _resolve_ws_url(cdp_port: int, *, timeout_s: float) -> str:
    pages = list_pages(_cdp_origin(cdp_port), timeout_s=timeout_s)
    page = _pick_grok_page(pages)
    if page is None:
        raise URLError("no Grok Bot CDP page on port")
    ws = page.get("web_socket_debugger_url") or ""
    if not ws:
        raise URLError("missing web_socket_debugger_url")
    return ws


async def _cdp_eval(
    ws_url: str,
    expression: str,
    *,
    await_promise: bool = True,
    timeout_s: float = 30.0,
) -> Any:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": await_promise,
                        "returnByValue": True,
                    },
                }
            )
        )
        raw = await asyncio.wait_for(_cdp_response(ws, 1), timeout_s)
    value = ((raw.get("result") or {}).get("result") or {}).get("value")
    if value is None:
        detail = (raw.get("result") or {}).get("exceptionDetails")
        raise RuntimeError(f"CDP evaluate failed: {detail or raw}")
    return value


async def _cdp_response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg


_PROBE_JS = """(async () => {
  const phase = await window.desktop.main.getBoxHostPhase();
  const egress = await window.desktop.main.getEgressTunnelStatus();
  const migration = await window.desktop.main.getBoxMigrationStatus();
  const snap = await window.desktop.main.getInitialSnapshot();
  const running_assistants = (snap.roster || [])
    .filter(r => r.runState?.state === "running")
    .map(r => ({id: r.id, name: r.name, state: r.runState}));
  return {phase, egress, migration, running_assistants};
})()"""


async def probe_grok_computer(
    cdp_port: int = 9339, display: str = ":9", *, timeout_s: float = 10.0
) -> dict[str, Any]:
    """Read box phase, egress, migration, and running assistants from :9339."""
    del display
    ws_url = _resolve_ws_url(cdp_port, timeout_s=min(timeout_s, 5.0))
    payload = await _cdp_eval(ws_url, _PROBE_JS, timeout_s=timeout_s)
    return payload if isinstance(payload, dict) else {}


_WAKE_JS = """(async (agentId) => {
  await window.desktop.main.focusWindow();
  await window.desktop.main.setComputerViewOpen({isOpen: true});
  await window.desktop.main.signalBoxIntent({agentId, intent: "composer_focus"});
  let egress = await window.desktop.main.getEgressTunnelStatus();
  if (egress?.state === "off") {
    await window.desktop.main.forceReconnectGateway();
    egress = await window.desktop.main.getEgressTunnelStatus();
  }
  const phase = await window.desktop.main.getBoxHostPhase();
  return {phase, egress};
})"""


async def wake_grok_computer(
    cdp_port: int = 9339,
    display: str = ":9",
    agent_id: str = _DEFAULT_AGENT_ID,
    *,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Reset X11 idle and signal presence on the Grok Bot desktop."""
    x11_reset = reset_x11_screensaver(display)
    ws_url = _resolve_ws_url(cdp_port, timeout_s=min(timeout_s, 5.0))
    expr = f"({ _WAKE_JS })({json.dumps(agent_id)})"
    wake = await _cdp_eval(ws_url, expr, timeout_s=timeout_s)
    if not isinstance(wake, dict):
        wake = {}
    return {**wake, "x11_reset": x11_reset}


_UPDATE_CLICK_JS = """(async () => {
  let btn = document.querySelector('button[aria-label="Update Bot\\'s Computer"]');
  if (!btn) {
    const km = Array.from(document.querySelectorAll('button,[role=button]'))
      .find(el => (el.getAttribute('aria-label')||'').trim() === 'KM');
    if (km) km.click();
    await new Promise(r => setTimeout(r, 200));
    const settings = Array.from(document.querySelectorAll('button,[role=menuitem],[role=button]'))
      .find(el => /^settings$/i.test((el.innerText||el.getAttribute('aria-label')||'').trim()));
    if (settings) settings.click();
    await new Promise(r => setTimeout(r, 300));
    const computer = Array.from(document.querySelectorAll('button,[role=tab],[role=button]'))
      .find(el => /^computer$/i.test((el.innerText||el.getAttribute('aria-label')||'').trim()));
    if (computer) computer.click();
    await new Promise(r => setTimeout(r, 300));
    btn = document.querySelector('button[aria-label="Update Bot\\'s Computer"]');
  }
  if (!btn) return {ok: false, reason: "update_button_missing"};
  btn.click();
  await new Promise(r => setTimeout(r, 400));
  const confirm = document.querySelector(
    'button[data-confirming="true"][aria-label="Click Again to Confirm: Update Bot\\'s Computer"]'
  );
  if (confirm) confirm.click();
  const migration = await window.desktop.main.getBoxMigrationStatus();
  return {ok: true, migration};
})()"""

_CLOSE_SETTINGS_JS = """(() => {
  const close = document.querySelector('button[aria-label="Close"]');
  if (close) { close.click(); return {closed: true}; }
  return {closed: false};
})()"""


async def _wait_phase_settled(
    ws_url: str, *, timeout_s: float
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = await _cdp_eval(ws_url, _PROBE_JS, timeout_s=5.0)
        if isinstance(probe, dict):
            phase = str(probe.get("phase") or "").lower()
            if phase in {"following", "settled"}:
                return probe
        await asyncio.sleep(0.5)
    return None


async def _poll_migration(
    ws_url: str, *, poll_timeout_s: float, interval_s: float = 3.0
) -> dict[str, Any]:
    deadline = time.monotonic() + poll_timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        raw = await _cdp_eval(
            ws_url,
            "window.desktop.main.getBoxMigrationStatus()",
            await_promise=True,
            timeout_s=5.0,
        )
        if isinstance(raw, dict):
            last = raw
            phase = str(raw.get("phase") or "")
            if phase in {"done", "failed"}:
                return last
        await asyncio.sleep(interval_s)
    return last


async def run_grok_computer_update(
    cdp_port: int = 9339,
    display: str = ":9",
    agent_id: str = _DEFAULT_AGENT_ID,
    require_idle: bool = True,
    poll_timeout_s: float = 180.0,
    *,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Run KM Settings Computer update with busy guard and post-close keep-alive."""
    probe = await probe_grok_computer(cdp_port=cdp_port, display=display, timeout_s=timeout_s)
    running = probe.get("running_assistants") or []
    if require_idle and running:
        return {"status": "busy_running_assistants", "running_assistants": running}

    ws_url = _resolve_ws_url(cdp_port, timeout_s=min(timeout_s, 5.0))
    pass1 = await _cdp_eval(ws_url, _UPDATE_CLICK_JS, timeout_s=max(timeout_s, 60.0))
    mig = (pass1.get("migration") if isinstance(pass1, dict) else None) or {}
    if (
        isinstance(mig, dict)
        and mig.get("phase") == "failed"
        and mig.get("detail") == _STORE_NOT_DURABLE
    ):
        await _wait_phase_settled(ws_url, timeout_s=5.0)
        pass1 = await _cdp_eval(ws_url, _UPDATE_CLICK_JS, timeout_s=max(timeout_s, 60.0))

    migration = await _poll_migration(ws_url, poll_timeout_s=poll_timeout_s)
    close = await _cdp_eval(ws_url, _CLOSE_SETTINGS_JS, await_promise=False, timeout_s=10.0)
    keep = await wake_grok_computer(
        cdp_port=cdp_port, display=display, agent_id=agent_id, timeout_s=timeout_s
    )
    status = "done" if migration.get("phase") == "done" else "failed"
    return {
        "status": status,
        "migration": migration,
        "pass1": pass1,
        "close": close,
        "keep_alive": keep,
        "probe_before": probe,
    }


__all__ = [
    "probe_grok_computer",
    "reset_x11_screensaver",
    "run_grok_computer_update",
    "wake_grok_computer",
]
