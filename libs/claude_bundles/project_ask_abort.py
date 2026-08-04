"""Abort cleanup for project-ask — Stop-then-attest, ownership guard (24911/24976)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

from claude_bundles import cdp_registry
from claude_bundles import cdp_registry_events as _events

_ABORT_STOP_TIMEOUT_S = 5.0
_ATTEST_POLL_INTERVAL_S = 0.25
_MAX_ATTEST_POLLS = 12
_ABORT_LOCK = threading.Lock()
_ABORT_DONE = False

AbortCleanupOutcome = Literal[
    "attested_stopped_and_deregistered",
    "still_attached",
    "probe_inconclusive",
    "stop_transport_failed",
    "stopped_deregister_failed",
    "detached_remote_running",
    "ownership_lost",
    "lane_inactive",
    "already_done",
    "no_registration",
]

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

_HAS_STOP_JS = """
(() => {
  const isStop = (b) => {
    const a = (b.getAttribute('aria-label') || '').trim();
    const t = (b.innerText || '').trim();
    return /^stop(\\s+(generating|response|generating response))?$/i.test(a)
      || /^stop(\\s+(generating|response|generating response))?$/i.test(t);
  };
  const chatInput = document.querySelector('[data-testid="chat-input"]');
  const main = document.querySelector('main') || document.querySelector('[role="main"]');
  if (!chatInput && !main) {
    return { hasStop: false, probeOk: false };
  }
  const roots = [];
  const seen = new Set();
  const add = (el) => { if (el && !seen.has(el)) { seen.add(el); roots.push(el); } };
  add(main);
  if (chatInput) {
    add(chatInput.closest('main'));
    add(chatInput.closest('form'));
  }
  for (const root of roots) {
    for (const b of root.querySelectorAll('button,[role=button]')) {
      if (isStop(b)) { return { hasStop: true, probeOk: true }; }
    }
  }
  return { hasStop: false, probeOk: true };
})()
"""


@dataclass(frozen=True)
class AttestResult:
    has_stop: bool
    probe_ok: bool
    stop_clicked: bool | None = None
    click_failed: bool = False


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
    """Whether exit deregister should kill Chrome / drop the CSE.

    Single-shot ``ask`` kills on exit. ``operator-proxy`` and ``mission`` must
    retain the CSE after first-leg content_proof (detach/orphan-alive OK) so
    nested Mode B can ``project_ask(op=followup)`` into the live window.
    """
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
    effective_purpose = purpose or reg.purpose
    kill = purpose_kill_default(effective_purpose)
    if cdp_registry.is_primary_profile(reg.profile):
        kill = False
    _events.emit(
        _events.cdp_port_exit_kill_decision(
            purpose=effective_purpose,
            registration_id=reg.registration_id,
            port=reg.port,
            kill=kill,
        )
    )
    cdp_registry.deregister_lane(reg.registration_id, kill=kill)
    cdp_registry.reclaim_best_effort()


def _claude_page(cdp_url: str) -> dict[str, Any] | None:
    base = cdp_url.rstrip("/")
    with urllib.request.urlopen(f"{base}/json", timeout=2) as resp:
        tabs: list[dict[str, Any]] = json.loads(resp.read())
    return next(
        (
            t
            for t in tabs
            if t.get("type") == "page" and "claude.ai" in t.get("url", "")
        ),
        None,
    )


async def _evaluate_js_async(cdp_url: str, expression: str) -> Any:
    import websockets

    page = _claude_page(cdp_url)
    if not page:
        return None
    async with websockets.connect(
        page["webSocketDebuggerUrl"], open_timeout=2, close_timeout=1
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True},
                }
            )
        )
        raw = await asyncio.wait_for(ws.recv(), timeout=2)
    payload = json.loads(raw)
    result = payload.get("result", {}).get("result", {})
    return result.get("value")


async def _probe_has_stop_async(cdp_url: str) -> tuple[bool, bool]:
    """Return (has_stop, probe_ok) — probe_ok requires composer root fidelity (H1)."""
    try:
        value = await _evaluate_js_async(cdp_url, _HAS_STOP_JS)
    except Exception:
        return True, False
    if not isinstance(value, dict):
        return True, False
    return bool(value.get("hasStop")), bool(value.get("probeOk"))


async def _click_stop_cdp_async(cdp_url: str) -> bool:
    try:
        value = await _evaluate_js_async(cdp_url, _STOP_CLICK_JS)
    except Exception:
        raise
    return bool(value)


async def _stop_and_attest_async(cdp_url: str, *, timeout_s: float) -> AttestResult:
    deadline = time.monotonic() + timeout_s
    has_stop, probe_ok = await _probe_has_stop_async(cdp_url)
    if not probe_ok:
        return AttestResult(has_stop=True, probe_ok=False)
    if not has_stop:
        return AttestResult(has_stop=False, probe_ok=True, stop_clicked=None)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return AttestResult(has_stop=True, probe_ok=True, click_failed=True)
    try:
        stop_clicked = await asyncio.wait_for(
            _click_stop_cdp_async(cdp_url), timeout=min(2.0, remaining)
        )
    except Exception:
        return AttestResult(has_stop=True, probe_ok=True, click_failed=True)
    polls = 0
    while polls < _MAX_ATTEST_POLLS and time.monotonic() < deadline:
        await asyncio.sleep(_ATTEST_POLL_INTERVAL_S)
        has_stop, probe_ok = await _probe_has_stop_async(cdp_url)
        if not probe_ok:
            return AttestResult(
                has_stop=True, probe_ok=False, stop_clicked=stop_clicked
            )
        if not has_stop:
            return AttestResult(
                has_stop=False, probe_ok=True, stop_clicked=stop_clicked
            )
        polls += 1
    return AttestResult(has_stop=True, probe_ok=True, stop_clicked=stop_clicked)


def bounded_stop_via_cdp(
    cdp_url: str, *, timeout_s: float = _ABORT_STOP_TIMEOUT_S
) -> AttestResult:
    """Bounded Stop + ¬hasStop attest — offloaded from the FastAPI event loop (L3)."""

    result_holder: list[AttestResult] = [
        AttestResult(has_stop=True, probe_ok=False, click_failed=True)
    ]

    def _run() -> None:
        try:
            result_holder[0] = asyncio.run(
                asyncio.wait_for(
                    _stop_and_attest_async(cdp_url, timeout_s=timeout_s),
                    timeout=timeout_s,
                )
            )
        except Exception:
            result_holder[0] = AttestResult(
                has_stop=True, probe_ok=False, click_failed=True
            )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s + 0.5)
    return result_holder[0]


def _try_deregister(
    reg: cdp_registry.Registration, *, purpose: str | None
) -> AbortCleanupOutcome:
    # M1: pre-click ¬hasStop (already_idle) folds here; aborted=True is deliberate
    # even when the stream finished naturally — operator abort semantics, not proof
    # the Stop click halted an active generation.
    try:
        deregister_on_exit(reg, purpose=purpose)
    except Exception:
        return "stopped_deregister_failed"
    global _ABORT_DONE
    with _ABORT_LOCK:
        _ABORT_DONE = True
    return "attested_stopped_and_deregistered"


def abort_cleanup(
    reg: cdp_registry.Registration, *, purpose: str | None
) -> AbortCleanupOutcome:
    with _ABORT_LOCK:
        if _ABORT_DONE:
            return "already_done"
    if not registration_owns_port(reg.registration_id, reg.port):
        return "ownership_lost"

    row = cdp_registry._load_active().get(reg.registration_id)
    if row is None or row.get("status") != "active":
        return "lane_inactive"

    if not _process_owns_driver(reg.registration_id):
        if cdp_registry.is_driver_lock_held(reg.registration_id):
            emit_detached_status(reg.registration_id)
            return "detached_remote_running"
        if not registration_owns_port(reg.registration_id, reg.port):
            return "ownership_lost"

    attest = bounded_stop_via_cdp(reg.cdp_url)
    if not attest.probe_ok:
        return "probe_inconclusive"
    if attest.click_failed and attest.has_stop:
        return "stop_transport_failed"
    if attest.has_stop:
        return "still_attached"
    return _try_deregister(reg, purpose=purpose)


def install_abort_handlers(
    reg: cdp_registry.Registration, *, purpose: str | None
) -> None:
    def _handle(_signum: int, _frame: object | None) -> None:
        outcome = abort_cleanup(reg, purpose=purpose)
        print(f"abort_cleanup outcome={outcome}", flush=True)

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGHUP, _handle)


def lookup_active_registration(
    registration_id: str,
) -> cdp_registry.Registration | None:
    for reg in cdp_registry.list_active():
        if reg.registration_id == registration_id:
            return reg
    return None


def abort_cleanup_registration_id(registration_id: str) -> int:
    reg = lookup_active_registration(registration_id)
    if reg is None:
        return 0
    outcome = abort_cleanup(reg, purpose=reg.purpose)
    print(f"abort_cleanup outcome={outcome}", flush=True)
    return 0
