"""1-to-1 Electron chat paste-and-read relay over CDP (gc1 <-> gc2 or cursor <-> grok).

Uses the shared ``cursor_bridge`` Electron window and sidebar agent selection
interface (`list_pages`, `probe_surface`, `select_sidebar_target`) to:
1. Select the bound agent row in a target Electron window (`:8`, `:9`, `:10`)
   when the currently active composer is empty.
2. Read the latest settled turn from `.sand-message-prose` once `generating` is
   `False`.
3. Paste any literal message text into a peer Electron window's selected agent
   composer and submit it, repeating 1-to-1 until the conversation moves to
   `agent_bus` + wakes (`HANDOFF_TO_BUS` or `BRIDGE_WAKE`).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError

from browser_governance.contract import (
    SUPERVISION_BANNER_SCAN_JS,
    build_supervision_envelope,
    unscanned_supervision,
)

from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.chat_surface import (
    _evaluate_cdp_js,
    maybe_emit_browser_supervision,
    probe_surface,
    select_sidebar_target,
    wake_decision,
)
from cursor_bridge.display_interaction_locks import (
    agent_paste_lock,
    display_switch_lock,
    paste_lock_key,
)
from cursor_bridge.dom_composer_paste import (
    composer_selector,
    composer_text_semantic_equals,
    dom_insert_and_send,
)
from cursor_bridge.electron_chat_grok_wait import (
    messages_fingerprint,
    probe_grok_after_select,
)
from cursor_bridge.grok_agent_ops import (
    grok_selection_current_agent_id_async,
    select_grok_agent_async,
)
from cursor_bridge.grok_computer import probe_grok_computer, resolve_grok_cdp_port
from cursor_bridge.wake_delivery import (
    _busy_or_draft_reason,
    _pick_page,
    _refuse,
    normalize_target,
)

HANDOFF_MARKERS = ("HANDOFF_TO_BUS", "BRIDGE_WAKE", "AGENT_BUS_READY")
_GROK_WINDOW_TITLE = "Grok Bot"


def _grok_restore_fields(
    *,
    restore_agent_id: str,
    did_switch: bool,
    target_agent_id: str,
) -> dict[str, Any]:
    return {
        "restore_agent_id": restore_agent_id,
        "did_switch": did_switch,
        "target_agent_id": target_agent_id,
    }


async def _restore_grok_prior_selection(
    display: str,
    restore_agent_id: str,
    target_agent_id: str,
    result: dict[str, Any] | None,
) -> None:
    """Return the window to ``restore_agent_id`` after commission on another agent."""
    if not restore_agent_id or restore_agent_id == target_agent_id:
        return
    try:
        picked = await select_grok_agent_async(display, restore_agent_id)
        if result is not None:
            result["restore_ok"] = bool(picked.get("ok"))
            if not picked.get("ok"):
                result["restore_error"] = str(
                    picked.get("code") or picked.get("reason") or "restore_failed"
                )
    except (TimeoutError, RuntimeError, OSError) as exc:
        if result is not None:
            result["restore_ok"] = False
            result["restore_error"] = type(exc).__name__


async def _grok_select_and_confirm(
    endpoint: ElectronEndpoint,
    *,
    ws_url: str,
    disp: str,
    grok_agent: str,
    surface: dict[str, Any],
    verdict: dict[str, Any],
    selection_prior_agent_id: str = "",
) -> dict[str, Any]:
    """Select ``grok_agent`` via CDP roster, confirm active surface, apply target guards."""
    surface_prior = str(surface.get("active_agent_id") or "").strip()
    prior = (selection_prior_agent_id or surface_prior).strip()
    did_switch = bool(prior and prior != grok_agent)
    async with display_switch_lock(disp):
        try:
            surface = await probe_surface(ws_url)
        except (TimeoutError, RuntimeError, OSError) as exc:
            return {
                "ok": False,
                "reason": "surface_probe_failed",
                "endpoint": endpoint.label,
                "display": disp,
                "app": "grok",
                "detail": type(exc).__name__,
                "did_switch": did_switch,
            }
        baseline_agent = str(surface.get("active_agent_id") or "").strip()
        prior_fp = messages_fingerprint(surface)
        prior_generating = bool(surface.get("generating"))
        cdp_pick = await select_grok_agent_async(disp, grok_agent)
        if not cdp_pick.get("ok"):
            try:
                fresh = await probe_surface(ws_url)
            except (TimeoutError, RuntimeError, OSError) as exc:
                return {
                    "ok": False,
                    "reason": "surface_probe_failed",
                    "endpoint": endpoint.label,
                    "display": disp,
                    "app": "grok",
                    "detail": type(exc).__name__,
                    "did_switch": did_switch,
                }
            pick_verdict = wake_decision(
                fresh,
                thread=endpoint.thread,
                slug=endpoint.slug,
                title=endpoint.title or None,
                agent_id=grok_agent,
            )
            if pick_verdict.get("ok") and not _busy_or_draft_reason(fresh):
                return {
                    "ok": True,
                    "surface": fresh,
                    "verdict": pick_verdict,
                    "selected": False,
                    "did_switch": did_switch,
                }
            pick_verdict = wake_decision(
                surface,
                thread=endpoint.thread,
                slug=endpoint.slug,
                title=endpoint.title or None,
                agent_id=grok_agent,
            )
            click_res = await select_sidebar_target(
                ws_url,
                expected_title=str(pick_verdict.get("expected_title") or ""),
                expected_agent_id=grok_agent,
            )
            if not click_res.get("clicked"):
                return {
                    "ok": False,
                    "reason": "selection_failed",
                    "code": str(cdp_pick.get("code") or "selection_failed"),
                    "endpoint": endpoint.label,
                    "display": disp,
                    "app": "grok",
                    "select": cdp_pick,
                    "did_switch": False,
                }
            await asyncio.sleep(0.25)
            try:
                surface = await probe_surface(ws_url)
            except (TimeoutError, RuntimeError, OSError) as exc:
                return {
                    "ok": False,
                    "reason": "surface_probe_failed",
                    "endpoint": endpoint.label,
                    "display": disp,
                    "app": "grok",
                    "detail": type(exc).__name__,
                    "did_switch": did_switch,
                }
            verdict = wake_decision(
                surface,
                thread=endpoint.thread,
                slug=endpoint.slug,
                title=endpoint.title or None,
                agent_id=grok_agent,
            )
            guard = _busy_or_draft_reason(surface)
            if guard:
                return {
                    "ok": False,
                    "reason": guard,
                    "endpoint": endpoint.label,
                    "display": disp,
                    "app": "grok",
                    "selected": True,
                    "did_switch": did_switch,
                }
            if not verdict.get("ok"):
                return {
                    **verdict,
                    "ok": False,
                    "endpoint": endpoint.label,
                    "display": disp,
                    "app": "grok",
                    "selected": True,
                    "did_switch": did_switch,
                }
            return {
                "ok": True,
                "surface": surface,
                "verdict": verdict,
                "selected": True,
                "did_switch": did_switch,
            }
    grok_wait = await probe_grok_after_select(
        ws_url,
        endpoint=endpoint,
        expected_agent_id=grok_agent,
        baseline_agent_id=baseline_agent,
        prior_message_fp=prior_fp,
        prior_generating=prior_generating,
        display=disp,
    )
    surface = grok_wait["surface"]
    verdict = grok_wait["verdict"]
    base = {
        "endpoint": endpoint.label,
        "display": disp,
        "app": "grok",
        "selected": True,
        "did_switch": did_switch,
    }
    if grok_wait.get("stale_dom"):
        unconfirmed: dict[str, Any] = {
            **base,
            "ok": False,
            "code": "conversation_unconfirmed",
            "reason": "conversation DOM stale after select",
            "active_agent_id": verdict.get("active_agent_id"),
            "stale_dom": True,
        }
        foreign = grok_wait.get("foreign_agent_id")
        if foreign:
            unconfirmed["foreign_agent_id"] = foreign
        return unconfirmed
    if not verdict.get("ok"):
        try:
            fresh_after_wait = await probe_surface(ws_url)
        except (TimeoutError, RuntimeError, OSError) as exc:
            return {
                "ok": False,
                "reason": "surface_probe_failed",
                "endpoint": endpoint.label,
                "display": disp,
                "app": "grok",
                "detail": type(exc).__name__,
                "did_switch": did_switch,
            }
        busy = _busy_or_draft_reason(fresh_after_wait)
        if busy:
            reprobe_verdict = wake_decision(
                fresh_after_wait,
                thread=endpoint.thread,
                slug=endpoint.slug,
                title=endpoint.title or None,
                agent_id=grok_agent,
            )
            return {
                **base,
                "ok": False,
                "reason": busy,
                "active_title": reprobe_verdict.get("active_title"),
                "active_agent_id": reprobe_verdict.get("active_agent_id"),
                "titles": reprobe_verdict.get("titles"),
                "did_switch": did_switch,
            }
        click_res: dict[str, Any] = {"clicked": False}
        async with display_switch_lock(disp):
            try:
                surface = await probe_surface(ws_url)
            except (TimeoutError, RuntimeError, OSError) as exc:
                return {
                    "ok": False,
                    "reason": "surface_probe_failed",
                    "endpoint": endpoint.label,
                    "display": disp,
                    "app": "grok",
                    "detail": type(exc).__name__,
                    "did_switch": did_switch,
                }
            verdict = wake_decision(
                surface,
                thread=endpoint.thread,
                slug=endpoint.slug,
                title=endpoint.title or None,
                agent_id=grok_agent,
            )
            guard = _busy_or_draft_reason(surface)
            if guard:
                return {
                    **base,
                    "ok": False,
                    "reason": guard,
                    "active_title": verdict.get("active_title"),
                    "active_agent_id": verdict.get("active_agent_id"),
                    "titles": verdict.get("titles"),
                    "did_switch": did_switch,
                }
            click_res = await select_sidebar_target(
                ws_url,
                expected_title=str(verdict.get("expected_title") or ""),
                expected_agent_id=grok_agent,
            )
        if click_res.get("clicked"):
            await asyncio.sleep(0.25)
            surface = await probe_surface(ws_url)
            verdict = wake_decision(
                surface,
                thread=endpoint.thread,
                slug=endpoint.slug,
                title=endpoint.title or None,
                agent_id=grok_agent,
            )
        if not verdict.get("ok"):
            return {
                **verdict,
                **base,
                "ok": False,
                "did_switch": did_switch,
            }
    guard = _busy_or_draft_reason(surface)
    if guard:
        return {
            **base,
            "ok": False,
            "reason": guard,
            "active_title": verdict.get("active_title"),
            "active_agent_id": verdict.get("active_agent_id"),
            "titles": verdict.get("titles"),
        }
    return {
        **base,
        "ok": True,
        "surface": surface,
        "verdict": verdict,
    }


def _grok_ws_url(cdp_port: int, *, timeout_s: float = 5.0) -> str:
    pages = list_pages(f"http://127.0.0.1:{cdp_port}", timeout_s=timeout_s)
    named = [
        p
        for p in pages
        if p.get("title") == _GROK_WINDOW_TITLE and p.get("web_socket_debugger_url")
    ]
    if len(named) == 1:
        page = named[0]
    else:
        usable = [p for p in pages if p.get("web_socket_debugger_url")]
        if len(usable) != 1:
            raise URLError("no Grok Bot CDP page on port")
        page = usable[0]
    ws = page.get("web_socket_debugger_url") or ""
    if not ws:
        raise URLError("missing web_socket_debugger_url")
    return ws


async def probe_electron_supervision(
    *,
    display: str,
    cdp_port: int | None = None,
    surface: str = "grok_electron",
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Read-only Grok Electron supervision (:9/:10) via desktop.main + banner scan."""
    from browser_governance.contract import supervision_gate_mode

    if supervision_gate_mode() != "observe":
        return unscanned_supervision(surface=surface, reason="gate_mode_off")
    disp = display if display.startswith(":") else f":{display.lstrip(':')}"
    port = resolve_grok_cdp_port(disp, cdp_port)
    try:
        probe = await probe_grok_computer(
            cdp_port=port,
            display=disp,
            agent_id=agent_id,
            timeout_s=10.0,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return unscanned_supervision(
            surface=surface,
            reason=f"probe_skipped:{type(exc).__name__}",
        )
    if not isinstance(probe, dict):
        return unscanned_supervision(surface=surface, reason="probe_empty")

    migration = probe.get("migration")
    migration_phase = migration.get("phase") if isinstance(migration, dict) else None
    app_health: dict[str, Any] = {
        "phase": probe.get("phase"),
        "egress": probe.get("egress"),
        "migration_phase": migration_phase,
        "forever_box": probe.get("forever_box"),
    }
    auth_state = "signed_in" if probe.get("selected_agent_id") else "unknown"

    ui_banner: dict[str, Any] | None = None
    try:
        ws_url = _grok_ws_url(port)
        banner_raw = await _evaluate_cdp_js(
            ws_url, SUPERVISION_BANNER_SCAN_JS, msg_id=1, timeout_s=10.0
        )
        if isinstance(banner_raw, dict):
            ui_banner = banner_raw
    except asyncio.CancelledError:
        raise
    except Exception:
        ui_banner = None

    envelope = build_supervision_envelope(
        surface=surface,
        auth_state=auth_state,
        visibility_state="unknown",
        ui_banner=ui_banner,
        app_health=app_health,
        reason=None if ui_banner is not None else "banner_scan_skipped",
    )
    maybe_emit_browser_supervision(envelope, display=disp, cdp_port=port)
    return envelope


@dataclass(frozen=True, slots=True)
class ElectronEndpoint:
    """Identifies one sidebar agent inside an X11 Electron window."""

    label: str
    app: str
    display: str
    title: str = ""
    agent_id: str = ""
    thread: str = ""
    slug: str = ""


def message_fingerprint(text: str) -> str:
    """Stable SHA-256 hex digest of normalized message prose."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def should_stop_for_bus_handoff(
    text: str, *, extra_markers: tuple[str, ...] = ()
) -> bool:
    """True when the turn text signals transition from paste relay to agent_bus."""
    body = (text or "").strip()
    if not body:
        return False
    markers = HANDOFF_MARKERS + tuple(m for m in extra_markers if m)
    return any(marker in body for marker in markers)


async def _resolve_and_select_endpoint(
    endpoint: ElectronEndpoint, *, select: bool
) -> dict[str, Any]:
    """Open CDP page, probe surface, and select ``endpoint`` if needed."""
    target = normalize_target(endpoint.display, endpoint.app)
    if not target.get("ok"):
        return {
            "ok": False,
            "reason": "unknown_target",
            "endpoint": endpoint.label,
            "display": target.get("display"),
            "app": target.get("app"),
        }
    disp, app = target["display"], target["app"]
    try:
        pages = list_pages(target["origin"])
    except URLError as exc:
        return {
            "ok": False,
            "reason": "debug_unreachable",
            "endpoint": endpoint.label,
            "display": disp,
            "app": app,
            "detail": type(exc).__name__,
        }
    page = _pick_page(pages, app)
    if page is None:
        return {
            "ok": False,
            "reason": "no_page",
            "endpoint": endpoint.label,
            "display": disp,
            "app": app,
            "pages": len(pages),
        }
    ws_url = page["web_socket_debugger_url"]
    try:
        surface = await probe_surface(ws_url)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return {
            "ok": False,
            "reason": "surface_probe_failed",
            "endpoint": endpoint.label,
            "display": disp,
            "app": app,
            "detail": type(exc).__name__,
        }
    grok_agent = str(endpoint.agent_id or "").strip()
    verdict = wake_decision(
        surface,
        thread=endpoint.thread,
        slug=endpoint.slug,
        title=endpoint.title or None,
        agent_id=endpoint.agent_id or None,
    )
    selection_prior = ""
    if app == "grok" and grok_agent:
        selection_prior = await grok_selection_current_agent_id_async(disp)
    surface_prior = str(surface.get("active_agent_id") or "").strip()
    prior_agent_id = selection_prior or surface_prior
    restore_agent_id = (
        prior_agent_id
        if app == "grok" and grok_agent and prior_agent_id and prior_agent_id != grok_agent
        else ""
    )
    did_switch = bool(restore_agent_id)
    selected = False
    restore_meta = _grok_restore_fields(
        restore_agent_id=restore_agent_id,
        did_switch=did_switch,
        target_agent_id=grok_agent,
    )
    need_grok_switch = (
        app == "grok" and grok_agent and select and prior_agent_id != grok_agent
    )
    if need_grok_switch:
        switched = await _grok_select_and_confirm(
            endpoint,
            ws_url=ws_url,
            disp=disp,
            grok_agent=grok_agent,
            surface=surface,
            verdict=verdict,
            selection_prior_agent_id=selection_prior,
        )
        if not bool(switched.get("did_switch")) and did_switch:
            switched = {**switched, "did_switch": did_switch}
        restore_meta = _grok_restore_fields(
            restore_agent_id=restore_agent_id,
            did_switch=did_switch,
            target_agent_id=grok_agent,
        )
        if not switched.get("ok"):
            return {**switched, **restore_meta}
        surface = switched["surface"]
        verdict = switched["verdict"]
        selected = True
    elif not verdict.get("ok") and select and verdict.get("state") == "chat_list":
        guard = _busy_or_draft_reason(surface)
        if guard:
            return {
                "ok": False,
                "reason": guard,
                "endpoint": endpoint.label,
                "display": disp,
                "app": app,
                "active_title": verdict.get("active_title"),
                "active_agent_id": verdict.get("active_agent_id"),
                "titles": verdict.get("titles"),
                **restore_meta,
            }
        click_res: dict[str, Any] = {"clicked": False}
        async with display_switch_lock(disp):
            try:
                surface = await probe_surface(ws_url)
            except (TimeoutError, RuntimeError, OSError) as exc:
                return {
                    "ok": False,
                    "reason": "surface_probe_failed",
                    "endpoint": endpoint.label,
                    "display": disp,
                    "app": app,
                    "detail": type(exc).__name__,
                    **restore_meta,
                }
            verdict = wake_decision(
                surface,
                thread=endpoint.thread,
                slug=endpoint.slug,
                title=endpoint.title or None,
                agent_id=endpoint.agent_id or None,
            )
            guard = _busy_or_draft_reason(surface)
            if guard:
                return {
                    "ok": False,
                    "reason": guard,
                    "endpoint": endpoint.label,
                    "display": disp,
                    "app": app,
                    "active_title": verdict.get("active_title"),
                    "active_agent_id": verdict.get("active_agent_id"),
                    "titles": verdict.get("titles"),
                    **restore_meta,
                }
            if verdict.get("ok"):
                click_res = {"clicked": False}
            else:
                click_res = await select_sidebar_target(
                    ws_url,
                    expected_title=str(verdict.get("expected_title") or ""),
                    expected_agent_id=str(verdict.get("expected_agent_id") or ""),
                )
        if click_res.get("clicked"):
            selected = True
            await asyncio.sleep(0.25)
            surface = await probe_surface(ws_url)
            verdict = wake_decision(
                surface,
                thread=endpoint.thread,
                slug=endpoint.slug,
                title=endpoint.title or None,
                agent_id=endpoint.agent_id or None,
            )
    if not verdict.get("ok"):
        return {
            **verdict,
            "ok": False,
            "endpoint": endpoint.label,
            "display": disp,
            "app": app,
            "selected": selected,
            **restore_meta,
            "did_switch": did_switch,
        }
    return {
        "ok": True,
        "ws_url": ws_url,
        "surface": surface,
        "verdict": verdict,
        "selected": selected,
        "display": disp,
        "app": app,
        **restore_meta,
        "did_switch": did_switch,
    }


async def read_endpoint_turn(
    endpoint: ElectronEndpoint, *, select: bool = True
) -> dict[str, Any]:
    """Select ``endpoint`` (if needed and safe) and return its latest prose state."""
    target = normalize_target(endpoint.display, endpoint.app)
    disp = str(target.get("display") or endpoint.display)
    lock_id = paste_lock_key(agent_id=endpoint.agent_id, thread=endpoint.thread)
    out: dict[str, Any] | None = None
    restore_agent_id = ""
    target_agent = str(endpoint.agent_id or "").strip()
    did_switch = False
    async with agent_paste_lock(disp, lock_id):
        try:
            res = await _resolve_and_select_endpoint(endpoint, select=select)
            restore_agent_id = str(res.get("restore_agent_id") or "")
            did_switch = bool(res.get("did_switch"))
            if not res.get("ok"):
                out = res
                return out
            surface, verdict = res["surface"], res["verdict"]
            if surface.get("stale_dom"):
                messages: list[str] = []
            else:
                messages = [
                    str(m).strip()
                    for m in (surface.get("recent_messages") or [])
                    if str(m).strip()
                ]
            latest = messages[-1] if messages else ""
            out = {
                "ok": True,
                "endpoint": endpoint.label,
                "display": res["display"],
                "app": res["app"],
                "selected": res["selected"],
                "active_title": verdict.get("active_title"),
                "active_agent_id": verdict.get("active_agent_id"),
                "generating": bool(surface.get("generating")),
                "composer_draft": str(surface.get("composer_draft") or ""),
                "recent_messages": messages,
                "latest_message": latest,
                "latest_fingerprint": message_fingerprint(latest) if latest else "",
                "restore_agent_id": restore_agent_id,
                "did_switch": did_switch,
            }
            if isinstance(surface, dict) and surface.get("supervision"):
                out["supervision"] = surface["supervision"]
            return out
        finally:
            if (
                endpoint.app == "grok"
                and restore_agent_id
                and restore_agent_id != target_agent
            ):
                await _restore_grok_prior_selection(
                    disp, restore_agent_id, target_agent, out
                )


def _paste_outcome_base(
    *,
    verdict: dict[str, Any],
    endpoint: ElectronEndpoint,
    disp: str,
    app: str,
    selected: bool,
) -> dict[str, Any]:
    return {
        **verdict,
        "selected": selected,
        "endpoint": endpoint.label,
        "display": disp,
        "app": app,
    }


def _user_match_count(text: str, surface: dict[str, Any]) -> int:
    """Count user-role turns equal to ``text``. Assistant echoes do not count."""
    count = 0
    for turn in surface.get("message_turns") or []:
        if not isinstance(turn, dict):
            continue
        if str(turn.get("role") or "") != "user":
            continue
        if composer_text_semantic_equals(text, str(turn.get("text") or "")):
            count += 1
    return count


def _agent_matches(expected: str, surface: dict[str, Any]) -> bool:
    expected_s = (expected or "").strip()
    active = str(surface.get("active_agent_id") or "").strip()
    return bool(expected_s) and active == expected_s


def _readback_outcome(
    text: str,
    post: dict[str, Any],
    base: dict[str, Any],
    *,
    baseline: int,
    expected_agent: str,
    pasted: bool,
    phase: str,
) -> dict[str, Any]:
    """Classify one probe. ``generating`` is informational and never delivery.

    Delivery requires a new user-role match and the same active agent id.
    An empty composer is not success.
    """
    generating = bool(post.get("generating"))
    if not _agent_matches(expected_agent, post):
        return {
            **base,
            "ok": False,
            "code": "unknown",
            "reason": "unknown",
            "phase": phase or "send_unknown",
            "pasted": pasted,
            "delivered": False,
            "generating": generating,
            "retryable": False,
        }
    if _user_match_count(text, post) > baseline:
        return {
            **base,
            "ok": True,
            "reason": "match",
            "pasted": True,
            "delivered": True,
            "phase": "submitted_visible",
            "generating": generating,
            "message": text,
            "fingerprint": message_fingerprint(text),
            "retryable": False,
        }
    draft = str(post.get("composer_draft") or "")
    if draft and composer_text_semantic_equals(draft, text):
        return {
            **base,
            "ok": False,
            "code": "send_unconfirmed",
            "reason": "send_unconfirmed",
            "phase": "inserted",
            "pasted": True,
            "inserted": True,
            "delivered": False,
            "generating": generating,
            "retryable": False,
        }
    return {
        **base,
        "ok": False,
        "code": "unknown",
        "reason": "unknown",
        "phase": phase or "send_unknown",
        "pasted": pasted,
        "delivered": False,
        "generating": generating,
        "retryable": False,
    }


async def _reconcile_uncertain_send(
    ws_url: str,
    text: str,
    base: dict[str, Any],
    *,
    baseline: int,
    expected_agent: str,
    pasted: bool,
    phase: str,
) -> dict[str, Any]:
    """One target-page readback. Does not insert or click again."""
    post = await probe_surface(ws_url)
    return _readback_outcome(
        text,
        post,
        base,
        baseline=baseline,
        expected_agent=expected_agent,
        pasted=pasted,
        phase=phase,
    )


async def paste_endpoint_message(
    endpoint: ElectronEndpoint, message: str, *, select: bool = True
) -> dict[str, Any]:
    """Select ``endpoint`` and paste + submit arbitrary ``message`` text."""
    text = message or ""
    if not text.strip():
        return _refuse("empty_message", endpoint=endpoint.label)
    target = normalize_target(endpoint.display, endpoint.app)
    disp = str(target.get("display") or endpoint.display)
    lock_id = paste_lock_key(agent_id=endpoint.agent_id, thread=endpoint.thread)
    out: dict[str, Any] | None = None
    restore_agent_id = ""
    target_agent = str(endpoint.agent_id or "").strip()
    did_switch = False
    async with agent_paste_lock(disp, lock_id):
        try:
            res = await _resolve_and_select_endpoint(endpoint, select=select)
            restore_agent_id = str(res.get("restore_agent_id") or "")
            did_switch = bool(res.get("did_switch"))
            if not res.get("ok"):
                out = {**res, "pasted": False, "delivered": False}
                return out
            ws_url, surface, verdict = res["ws_url"], res["surface"], res["verdict"]
            disp, app, selected = res["display"], res["app"], res["selected"]
            base = _paste_outcome_base(
                verdict=verdict,
                endpoint=endpoint,
                disp=disp,
                app=app,
                selected=selected,
            )
            expected = str(
                endpoint.agent_id or verdict.get("expected_agent_id") or ""
            ).strip()
            active = str(verdict.get("active_agent_id") or "").strip()
            if expected and active and active != expected:
                return {
                    **base,
                    "ok": False,
                    "code": "target_mismatch",
                    "reason": "target_mismatch",
                    "pasted": False,
                    "delivered": False,
                }
            guard = _busy_or_draft_reason(surface)
            if guard:
                refused = _refuse(
                    guard, endpoint=endpoint.label, display=disp, app=app, selected=selected
                )
                return {**refused, "code": refused.get("reason")}
            if app not in {"grok", "cursor"}:
                return {
                    **_refuse(
                        "composer_unconfirmed",
                        endpoint=endpoint.label,
                        display=disp,
                        app=app,
                    ),
                    "code": "composer_unconfirmed",
                }
            sel = composer_selector(
                app=app,
                agent_id=str(verdict.get("expected_agent_id") or endpoint.agent_id or ""),
            )
            draft_writer = f"interlocutor:{endpoint.agent_id or endpoint.label}"
            baseline = _user_match_count(text, surface if isinstance(surface, dict) else {})
            try:
                if app == "cursor":
                    from cursor_bridge.wake_delivery import _paste_cursor_services

                    paste_res = await _paste_cursor_services(
                        ws_url, text, agent_id=str(endpoint.agent_id or "")
                    )
                else:
                    paste_res = await dom_insert_and_send(
                        ws_url,
                        selector=sel,
                        line=text,
                        preserve_newlines=True,
                        draft_writer=draft_writer,
                    )
            except TimeoutError:
                return await _reconcile_uncertain_send(
                    ws_url,
                    text,
                    base,
                    baseline=baseline,
                    expected_agent=expected,
                    pasted=False,
                    phase="send_unknown",
                )
            except (RuntimeError, OSError) as exc:
                return {
                    **_refuse(
                        "paste_failed",
                        endpoint=endpoint.label,
                        display=disp,
                        app=app,
                        detail=type(exc).__name__,
                    ),
                    "code": "paste_failed",
                    "phase": "insertion_failed",
                    "pasted": False,
                    "delivered": False,
                }
            phase = str(paste_res.get("phase") or "")
            inserted = bool(paste_res.get("inserted") or paste_res.get("pasted"))
            reason = str(paste_res.get("reason") or paste_res.get("code") or "")
            uncertain = (
                bool(paste_res.get("clicked_unknown"))
                or reason
                in {
                    "cdp_eval_timeout",
                    "clicked_unknown",
                }
                or phase == "send_unknown"
            )
            if str(paste_res.get("race_winner") or "") == "timer":
                return {
                    **base,
                    "ok": False,
                    "code": "delivery_unconfirmed",
                    "reason": "delivery_unconfirmed",
                    "pasted": True,
                    "delivered": False,
                    "retryable": False,
                    "phase": "send_unknown",
                    "race_winner": "timer",
                    "generating": bool(surface.get("generating"))
                    if isinstance(surface, dict)
                    else False,
                }
            if not paste_res.get("ok"):
                if uncertain:
                    reconciled = await _reconcile_uncertain_send(
                        ws_url,
                        text,
                        base,
                        baseline=baseline,
                        expected_agent=expected,
                        pasted=True,
                        phase="send_unknown",
                    )
                    if paste_res.get("clicked_unknown"):
                        reconciled["clicked_unknown"] = True
                    return reconciled
                return {
                    **base,
                    "ok": False,
                    "code": reason or "no_composer",
                    "reason": reason or "no_composer",
                    "pasted": inserted,
                    "inserted": inserted,
                    "delivered": False,
                    "phase": phase or ("inserted" if inserted else "insertion_failed"),
                    **({"retryable": False} if paste_res.get("retryable") is False else {}),
                }
            await asyncio.sleep(0.25)
            post = await probe_surface(ws_url)
            outcome = _readback_outcome(
                text,
                post,
                base,
                baseline=baseline,
                expected_agent=expected,
                pasted=True,
                phase="send_unknown",
            )
            if outcome.get("ok") and verdict.get("reason"):
                outcome["reason"] = verdict.get("reason")
            out = outcome
            return out
        finally:
            if (
                endpoint.app == "grok"
                and restore_agent_id
                and restore_agent_id != target_agent
            ):
                await _restore_grok_prior_selection(
                    disp, restore_agent_id, target_agent, out
                )


async def wait_for_new_settled_turn(
    endpoint: ElectronEndpoint,
    *,
    baseline_fingerprint: str,
    poll_interval_s: float = 2.0,
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    """Poll ``endpoint`` until ``generating`` is False and a new turn appears."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        snap = await read_endpoint_turn(endpoint, select=True)
        if not snap.get("ok"):
            return snap
        fp = str(snap.get("latest_fingerprint") or "")
        if not snap.get("generating") and fp and fp != baseline_fingerprint:
            return snap
        await asyncio.sleep(poll_interval_s)
    return {
        "ok": False,
        "reason": "turn_wait_timeout",
        "endpoint": endpoint.label,
        "baseline_fingerprint": baseline_fingerprint,
    }


async def run_one_to_one_relay(
    left: ElectronEndpoint,
    right: ElectronEndpoint,
    *,
    seed_message: str | None = None,
    max_exchanges: int = 10,
    poll_interval_s: float = 2.0,
    turn_timeout_s: float = 180.0,
    extra_stop_markers: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Repeatable 1-to-1 copy-and-paste relay between ``left`` (gc1) and ``right`` (gc2)."""
    transcript: list[dict[str, Any]] = []
    left_snap = await read_endpoint_turn(left, select=True)
    if not left_snap.get("ok"):
        return {
            "ok": False,
            "stage": "init_left",
            "detail": left_snap,
            "transcript": transcript,
        }
    right_snap = await read_endpoint_turn(right, select=True)
    if not right_snap.get("ok"):
        return {
            "ok": False,
            "stage": "init_right",
            "detail": right_snap,
            "transcript": transcript,
        }

    last_fp: dict[str, str] = {
        left.label: str(left_snap.get("latest_fingerprint") or ""),
        right.label: str(right_snap.get("latest_fingerprint") or ""),
    }
    speaker, listener = left, right
    if seed_message and seed_message.strip():
        pasted = await paste_endpoint_message(left, seed_message.strip(), select=True)
        if not pasted.get("ok"):
            return {
                "ok": False,
                "stage": "seed_paste",
                "detail": pasted,
                "transcript": transcript,
            }
        last_fp[left.label] = str(pasted.get("fingerprint") or "")
        transcript.append(
            {
                "step": 0,
                "from": "seed",
                "to": left.label,
                "message": pasted.get("message"),
            }
        )

    for step in range(1, max_exchanges + 1):
        turn = await wait_for_new_settled_turn(
            speaker,
            baseline_fingerprint=last_fp[speaker.label],
            poll_interval_s=poll_interval_s,
            timeout_s=turn_timeout_s,
        )
        if not turn.get("ok"):
            return {
                "ok": False,
                "stage": f"wait_turn_{step}_{speaker.label}",
                "detail": turn,
                "transcript": transcript,
            }
        reply_text = str(turn.get("latest_message") or "").strip()
        last_fp[speaker.label] = str(turn.get("latest_fingerprint") or "")
        if should_stop_for_bus_handoff(reply_text, extra_markers=extra_stop_markers):
            transcript.append(
                {
                    "step": step,
                    "from": speaker.label,
                    "to": "agent_bus",
                    "message": reply_text,
                    "handoff": True,
                }
            )
            return {
                "ok": True,
                "stopped_reason": "bus_handoff",
                "step": step,
                "speaker": speaker.label,
                "transcript": transcript,
            }
        listener_snap = await read_endpoint_turn(listener, select=True)
        if listener_snap.get("ok"):
            last_fp[listener.label] = str(listener_snap.get("latest_fingerprint") or "")
        pasted = await paste_endpoint_message(listener, reply_text, select=True)
        if not pasted.get("ok"):
            return {
                "ok": False,
                "stage": f"paste_step_{step}_{listener.label}",
                "detail": pasted,
                "transcript": transcript,
            }
        last_fp[listener.label] = str(pasted.get("fingerprint") or "")
        transcript.append(
            {
                "step": step,
                "from": speaker.label,
                "to": listener.label,
                "message": reply_text,
            }
        )
        speaker, listener = listener, speaker

    return {
        "ok": True,
        "stopped_reason": "max_exchanges_reached",
        "step": max_exchanges,
        "transcript": transcript,
    }


__all__ = [
    "ElectronEndpoint",
    "HANDOFF_MARKERS",
    "message_fingerprint",
    "paste_endpoint_message",
    "probe_electron_supervision",
    "read_endpoint_turn",
    "run_one_to_one_relay",
    "should_stop_for_bus_handoff",
    "wait_for_new_settled_turn",
]
