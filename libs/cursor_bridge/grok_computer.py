"""X11 idle reset and Grok Bot desktop.main CDP helpers for configured windows.

Callers are the grokbot-computer-update pipeline and seats that probe or
wake a Grok Bot display. Display numbers map to CDP ports through
``config/grok-windows.json``. Update clicks the Computer row inside the
Grok Bot settings dialog, ignores a stale migration ``done``, and closes
that dialog so the main portal is what stays on screen.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import json
import time
from typing import Any
from urllib.error import URLError

from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.grok_computer_page import (
    PROBE_JS as _PROBE_JS,
)
from cursor_bridge.grok_computer_page import (
    RETURN_TO_PORTAL_JS as _RETURN_TO_PORTAL_JS,
)
from cursor_bridge.grok_computer_page import (
    UPDATE_CLICK_JS as _UPDATE_CLICK_JS,
)
from cursor_bridge.grok_computer_page import (
    WAKE_JS as _WAKE_JS,
)
from cursor_bridge.grok_window_bindings import cdp_port_for_display

_DEFAULT_AGENT_ID = "b5c63fe1-61b5-47d6-be6e-319ec999c383"
_DEFAULT_CDP_PORT = 9339
_GROK_WINDOW_TITLE = "Grok Bot"
_STORE_NOT_DURABLE = "store-not-durable"
_MIGRATION_GRACE_S = 8.0


def resolve_grok_cdp_port(display: str, cdp_port: object | None = None) -> int:
    """Pick the CDP port for a Grok Bot display.

    A port listed in ``config/grok-windows.json`` replaces a missing port
    and the pipeline default 9339 when that display is configured elsewhere.
    Any other explicit port is left unchanged.
    """
    mapped = cdp_port_for_display(display)
    if cdp_port is None:
        return mapped if mapped is not None else _DEFAULT_CDP_PORT
    port = int(cdp_port)
    if mapped is not None and port == _DEFAULT_CDP_PORT and mapped != _DEFAULT_CDP_PORT:
        return mapped
    return port


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


async def probe_grok_computer(
    cdp_port: int = 9339, display: str = ":9", *, timeout_s: float = 10.0
) -> dict[str, Any]:
    """Read box phase, egress, migration, and running assistants over CDP.

    ``display`` is accepted for call-site symmetry and is not sent to the page.
    The websocket is chosen from ``cdp_port``.
    """
    del display
    ws_url = _resolve_ws_url(cdp_port, timeout_s=min(timeout_s, 5.0))
    payload = await _cdp_eval(ws_url, _PROBE_JS, timeout_s=timeout_s)
    return payload if isinstance(payload, dict) else {}


async def wake_grok_computer(
    cdp_port: int = 9339,
    display: str = ":9",
    agent_id: str = _DEFAULT_AGENT_ID,
    *,
    open_computer_view: bool = True,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Reset X11 idle and signal presence on the Grok Bot desktop.

    ``open_computer_view`` false closes the computer view so a post-update
    keep-alive leaves the main portal on the display. Side effect: X11
    screensaver reset on ``display`` and a CDP evaluate on ``cdp_port``.
    """
    x11_reset = reset_x11_screensaver(display)
    ws_url = _resolve_ws_url(cdp_port, timeout_s=min(timeout_s, 5.0))
    expr = f"({_WAKE_JS})({json.dumps(agent_id)}, {json.dumps(open_computer_view)})"
    wake = await _cdp_eval(ws_url, expr, timeout_s=timeout_s)
    if not isinstance(wake, dict):
        wake = {}
    return {**wake, "x11_reset": x11_reset}


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


def _migration_operation_id(raw: object) -> str:
    if not isinstance(raw, dict):
        return ""
    op = raw.get("operationId")
    if isinstance(op, dict):
        return str(op.get("value") or "")
    if op is None:
        return ""
    return str(op)


def _migration_moved(
    raw: dict[str, Any], baseline_operation_id: str, saw_active: bool
) -> bool:
    phase = str(raw.get("phase") or "")
    op = _migration_operation_id(raw)
    if phase not in {"done", "failed"}:
        return False
    if saw_active:
        return True
    return bool(op) and op != baseline_operation_id


async def _poll_migration(
    ws_url: str,
    *,
    poll_timeout_s: float,
    baseline_operation_id: str = "",
    interval_s: float = 3.0,
    grace_s: float = _MIGRATION_GRACE_S,
) -> dict[str, Any]:
    """Wait until this click's migration finishes.

    A ``done`` or ``failed`` row that still carries ``baseline_operation_id``
    is the previous operation. It becomes ``unchanged`` after ``grace_s``
    unless an in-progress phase was observed first.
    """
    deadline = time.monotonic() + poll_timeout_s
    started = time.monotonic()
    last: dict[str, Any] = {}
    saw_active = False
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
            if phase not in {"done", "failed", "idle", ""}:
                saw_active = True
            if _migration_moved(raw, baseline_operation_id, saw_active):
                return last
            elapsed = time.monotonic() - started
            if (
                not saw_active
                and _migration_operation_id(raw) == baseline_operation_id
                and phase in {"done", "failed", "idle", ""}
                and elapsed >= grace_s
            ):
                return {**last, "phase": "unchanged"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval_s, remaining))
    if (
        not saw_active
        and _migration_operation_id(last) == baseline_operation_id
        and str(last.get("phase") or "") in {"done", "failed", "idle", ""}
    ):
        return {**last, "phase": "unchanged"}
    return last


def _forever_box_update_pending(probe: dict[str, Any]) -> bool:
    forever_box = probe.get("forever_box")
    if not isinstance(forever_box, dict):
        return False
    return bool(forever_box.get("imageUpdateAvailable"))


async def _run_update_pass(ws_url: str, *, timeout_s: float) -> dict[str, Any]:
    pass1 = await _cdp_eval(ws_url, _UPDATE_CLICK_JS, timeout_s=max(timeout_s, 60.0))
    mig = (pass1.get("migration") if isinstance(pass1, dict) else None) or {}
    if (
        isinstance(mig, dict)
        and mig.get("phase") == "failed"
        and mig.get("detail") == _STORE_NOT_DURABLE
    ):
        await _wait_phase_settled(ws_url, timeout_s=5.0)
        pass1 = await _cdp_eval(
            ws_url, _UPDATE_CLICK_JS, timeout_s=max(timeout_s, 60.0)
        )
    return pass1 if isinstance(pass1, dict) else {}


def _update_result_status(
    *,
    migration: dict[str, Any],
    passes: list[dict[str, Any]],
    image_pending: bool,
    image_pending_before: bool,
) -> str:
    phase = str(migration.get("phase") or "")
    last_ok = bool(passes and passes[-1].get("ok"))
    if phase == "unchanged":
        return "up_to_date" if not image_pending else "failed"
    if phase == "failed" or not last_ok:
        if not last_ok and not image_pending_before and not image_pending:
            return "up_to_date"
        return "failed"
    if phase == "done" and not image_pending:
        return "done"
    if image_pending:
        return "failed"
    return "done" if phase == "done" else "failed"


async def run_grok_computer_update(
    cdp_port: int = 9339,
    display: str = ":9",
    agent_id: str = _DEFAULT_AGENT_ID,
    require_idle: bool = True,
    poll_timeout_s: float = 180.0,
    *,
    timeout_s: float = 30.0,
    max_update_passes: int = 2,
) -> dict[str, Any]:
    """Run the settings Computer update and leave the main portal on screen.

    ``poll_timeout_s`` is one shared budget for every pass, so a second pass
    cannot consume the pipeline timeout before the settings dialog is closed.
    A migration row that never leaves the pre-click operation id is
    ``unchanged`` and is not reported as done. When the image flag stays set
    after a real ``done``, one more pass runs.
    """
    probe = await probe_grok_computer(
        cdp_port=cdp_port, display=display, timeout_s=timeout_s
    )
    running = probe.get("running_assistants") or []
    if require_idle and running:
        return {"status": "busy_running_assistants", "running_assistants": running}

    image_pending_before = _forever_box_update_pending(probe)
    baseline = _migration_operation_id(probe.get("migration"))
    ws_url = _resolve_ws_url(cdp_port, timeout_s=min(timeout_s, 5.0))
    passes: list[dict[str, Any]] = []
    migration: dict[str, Any] = {}
    budget_deadline = time.monotonic() + poll_timeout_s
    for _attempt in range(max(1, max_update_passes)):
        pass_out = await _run_update_pass(ws_url, timeout_s=timeout_s)
        passes.append(pass_out)
        if not pass_out.get("ok"):
            break
        remaining = budget_deadline - time.monotonic()
        if remaining <= 0:
            break
        migration = await _poll_migration(
            ws_url,
            poll_timeout_s=remaining,
            baseline_operation_id=baseline,
        )
        phase = str(migration.get("phase") or "")
        if phase == "done":
            baseline = _migration_operation_id(migration) or baseline
        elif phase == "unchanged":
            continue
        else:
            break
        probe_after = await probe_grok_computer(
            cdp_port=cdp_port, display=display, timeout_s=timeout_s
        )
        if not _forever_box_update_pending(probe_after):
            break

    close = await _cdp_eval(ws_url, _RETURN_TO_PORTAL_JS, timeout_s=15.0)
    keep = await wake_grok_computer(
        cdp_port=cdp_port,
        display=display,
        agent_id=agent_id,
        open_computer_view=False,
        timeout_s=timeout_s,
    )
    probe_after = await probe_grok_computer(
        cdp_port=cdp_port, display=display, timeout_s=timeout_s
    )
    image_pending = _forever_box_update_pending(probe_after)
    status = _update_result_status(
        migration=migration,
        passes=passes,
        image_pending=image_pending,
        image_pending_before=image_pending_before,
    )
    return {
        "status": status,
        "migration": migration,
        "passes": passes,
        "pass1": passes[0] if passes else {},
        "close": close,
        "keep_alive": keep,
        "probe_before": probe,
        "probe_after": probe_after,
    }


__all__ = [
    "probe_grok_computer",
    "reset_x11_screensaver",
    "resolve_grok_cdp_port",
    "run_grok_computer_update",
    "wake_grok_computer",
]
