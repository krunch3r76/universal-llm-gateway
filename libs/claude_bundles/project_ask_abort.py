"""Abort cleanup for project-ask — Stop-then-kill, ownership guard (24911/24976)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import threading
import urllib.request
from typing import Any

from claude_bundles import cdp_registry

_ABORT_STOP_TIMEOUT_S = 5.0
_ABORT_LOCK = threading.Lock()
_ABORT_DONE = False

_STOP_CLICK_JS = """
(() => {
  const isStop = (b) => {
    const a = (b.getAttribute('aria-label') || '').trim();
    const t = (b.innerText || '').trim();
    return /^stop(\\s+(generating|response|generating response))?$/i.test(a)
      || /^stop(\\s+(generating|response|generating response))?$/i.test(t);
  };
  const roots = [];
  const seen = new Set();
  const add = (el) => { if (el && !seen.has(el)) { seen.add(el); roots.push(el); } };
  add(document.querySelector('main'));
  add(document.querySelector('[role="main"]'));
  const chatInput = document.querySelector('[data-testid="chat-input"]');
  if (chatInput) {
    add(chatInput.closest('main'));
    add(chatInput.closest('form'));
  }
  for (const root of roots) {
    for (const b of root.querySelectorAll('button,[role=button]')) {
      if (isStop(b)) { b.click(); return true; }
    }
  }
  return false;
})()
"""


def registration_owns_port(registration_id: str, expected_port: int) -> bool:
    """F2: registration must still be active on the expected port before kill."""
    try:
        row = cdp_registry._load_active().get(registration_id)
    except Exception:
        return False
    if row is None or row.get("status") != "active":
        return False
    return int(row.get("port", -1)) == expected_port


def purpose_kill_default(purpose: str | None) -> bool:
    return (purpose or "") == "ask"


def emit_detached_status(registration_id: str) -> None:
    """Best-effort stdout signal — remote driver still authoritative (24976 F1)."""
    print(
        f"status=detached_remote_running registration_id={registration_id}",
        flush=True,
    )


def _process_owns_driver(registration_id: str) -> bool:
    if cdp_registry.process_holds_driver_lock(registration_id):
        return True
    try:
        row = cdp_registry._load_active().get(registration_id)
    except Exception:
        return False
    if row is None:
        return False
    holder_pid = row.get("holder_pid")
    return isinstance(holder_pid, int) and holder_pid == os.getpid()


def deregister_on_exit(reg: cdp_registry.Registration, *, purpose: str | None) -> None:
    if not registration_owns_port(reg.registration_id, reg.port):
        return
    kill = purpose_kill_default(purpose or reg.purpose)
    cdp_registry.deregister_lane(reg.registration_id, kill=kill)


async def _click_stop_cdp_async(cdp_url: str) -> None:
    import websockets

    base = cdp_url.rstrip("/")
    with urllib.request.urlopen(f"{base}/json", timeout=2) as resp:
        tabs: list[dict[str, Any]] = json.loads(resp.read())
    page = next(
        (
            t
            for t in tabs
            if t.get("type") == "page" and "claude.ai" in t.get("url", "")
        ),
        None,
    )
    if not page:
        return
    async with websockets.connect(
        page["webSocketDebuggerUrl"], open_timeout=2, close_timeout=1
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": _STOP_CLICK_JS, "returnByValue": True},
                }
            )
        )
        await asyncio.wait_for(ws.recv(), timeout=2)


def bounded_stop_via_cdp(
    cdp_url: str, *, timeout_s: float = _ABORT_STOP_TIMEOUT_S
) -> None:
    """F3: bounded Stop before kill — local Chrome only; server-side burn is residual."""

    def _run() -> None:
        try:
            asyncio.run(
                asyncio.wait_for(_click_stop_cdp_async(cdp_url), timeout=timeout_s)
            )
        except Exception:
            pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s + 0.5)


def abort_cleanup(reg: cdp_registry.Registration, *, purpose: str | None) -> None:
    global _ABORT_DONE
    with _ABORT_LOCK:
        if _ABORT_DONE:
            return
        _ABORT_DONE = True
    if not registration_owns_port(reg.registration_id, reg.port):
        return

    row = cdp_registry._load_active().get(reg.registration_id)
    if row is None or row.get("status") != "active":
        return

    if not _process_owns_driver(reg.registration_id):
        if cdp_registry.is_driver_lock_held(reg.registration_id):
            emit_detached_status(reg.registration_id)
            return
        if not registration_owns_port(reg.registration_id, reg.port):
            return

    bounded_stop_via_cdp(reg.cdp_url)
    deregister_on_exit(reg, purpose=purpose)


def install_abort_handlers(
    reg: cdp_registry.Registration, *, purpose: str | None
) -> None:
    def _handle(_signum: int, _frame: object | None) -> None:
        abort_cleanup(reg, purpose=purpose)

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGHUP, _handle)


def lookup_active_registration(registration_id: str) -> cdp_registry.Registration | None:
    for reg in cdp_registry.list_active():
        if reg.registration_id == registration_id:
            return reg
    return None


def abort_cleanup_registration_id(registration_id: str) -> int:
    reg = lookup_active_registration(registration_id)
    if reg is None:
        return 0
    abort_cleanup(reg, purpose=reg.purpose)
    return 0
