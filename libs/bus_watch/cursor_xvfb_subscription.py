"""Auto-subscribe Cursor-on-Xvfb (:8) continuity houses to endpoint wake routing."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx

from bus_watch.cursor_vscdb import TRANSCRIPT_UUID_RE
from bus_watch.digest_budget import _bus, _get
from bus_watch.fable_lock import WATCH_DIR
from bus_watch.tick_state import update_state

_HOLDER_RE = re.compile(r"^ide:(?P<uuid>[0-9a-f-]{36})$", re.I)
_UUID_RE = TRANSCRIPT_UUID_RE


def parse_ide_holder_agent_id(holder: str) -> str | None:
    """Return transcript UUID from ``ide:<uuid>`` holder, else ``None``."""
    m = _HOLDER_RE.match(str(holder or "").strip())
    return m.group("uuid") if m else None


def cursor_xvfb_display_for_gui_host(gui_host: str | None) -> str:
    """Endpoint subscription display label for a Glass GUI host."""
    from cursor_bridge.cursor_window_config import subscription_display_for_gui_host

    return subscription_display_for_gui_host(gui_host)


def cursor_xvfb_subscription_body(
    *,
    thread_scope: str,
    agent_id: str,
    display: str = "8",
    title: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """JSON body for ``POST /endpoint-subscriptions`` on a Glass display."""
    root = str(thread_scope).strip()
    aid = str(agent_id).strip()
    disp = str(display or "8").strip() or "8"
    if not root or not aid:
        raise ValueError("thread_scope and agent_id required")
    return {
        "agent": "cursor",
        "thread_scope": root,
        "surface": "cursor",
        "display": disp,
        "agent_id": aid,
        "title": title if title is not None else f"{root} ",
        "bound_by": bound_by,
        "evidence": evidence,
    }


def subscribe_cursor_xvfb_house(
    thread_scope: str,
    agent_id: str,
    *,
    display: str = "8",
    title: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Upsert ``cursor`` Glass subscription for one continuity root."""
    body = cursor_xvfb_subscription_body(
        thread_scope=thread_scope,
        agent_id=agent_id,
        display=display,
        title=title,
        bound_by=bound_by,
        evidence=evidence,
    )
    owns = client is None
    try:
        if owns:
            with _bus() as bus:
                resp = bus.post("/endpoint-subscriptions", json=body)
        else:
            resp = client.post("/endpoint-subscriptions", json=body)
    except (Exception, SystemExit) as exc:
        if isinstance(exc, httpx.HTTPError):
            return {
                "ok": False,
                "reason": f"subscription_http:{type(exc).__name__}",
                "detail": str(exc)[:200],
            }
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "reason": f"subscription_failed:{type(exc).__name__}",
        }
    payload: dict[str, Any] = {}
    try:
        payload = resp.json()
    except ValueError:
        payload = {"raw": resp.text[:500]}
    ok = resp.status_code < 400
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "body": payload,
        "agent_id": body["agent_id"],
        "thread_scope": body["thread_scope"],
    }


def unsubscribe_cursor_xvfb_house(
    thread_scope: str,
    agent_id: str,
    *,
    client: httpx.Client | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Remove a Glass subscription when spawn launch fails after upsert."""
    root = str(thread_scope).strip()
    aid = str(agent_id).strip()
    if not root or not aid:
        return {"ok": False, "reason": "thread_scope_and_agent_id_required"}
    payload = {
        "agent": "cursor",
        "thread_scope": root,
        "bound_by": bound_by or "cursor-xvfb-subscription",
        "evidence": evidence,
        "expected_agent_id": aid,
    }
    owns = client is None
    try:
        if owns:
            with _bus() as bus:
                resp = bus.post("/endpoint-subscriptions/unsubscribe", json=payload)
        else:
            resp = client.post("/endpoint-subscriptions/unsubscribe", json=payload)
    except (Exception, SystemExit) as exc:
        if isinstance(exc, httpx.HTTPError):
            return {
                "ok": False,
                "reason": f"unsubscribe_http:{type(exc).__name__}",
                "detail": str(exc)[:200],
            }
        return {
            "ok": False,
            "reason": f"unsubscribe_failed:{type(exc).__name__}",
            "detail": str(exc)[:200],
        }
    ok = resp.status_code < 400
    body: dict[str, Any] = {}
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:500]}
    return {"ok": ok, "status_code": resp.status_code, "body": body}


def subscribe_cursor_xvfb_from_holder(
    root_id: str,
    holder: str,
    *,
    display: str = "8",
    title: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Subscribe when ``holder`` is ``ide:<transcript-uuid>``."""
    aid = parse_ide_holder_agent_id(holder)
    if not aid:
        return {"ok": False, "reason": "holder_not_ide_uuid", "holder": holder}
    return subscribe_cursor_xvfb_house(
        root_id,
        aid,
        display=display,
        title=title,
        bound_by=bound_by,
        evidence=evidence,
        client=client,
    )


def _normalize_gui_host(gui_host: str | None) -> str:
    from cursor_bridge.cursor_window_config import normalize_gui_host

    return normalize_gui_host(gui_host)


def gui_host_from_process_env() -> str | None:
    vscode_agent = os.environ.get("VSCODE_AGENT_FOLDER", "")
    ripgrep = os.environ.get("CURSOR_RIPGREP_PATH", "")
    if ".cursor-server" in vscode_agent or ".cursor-server" in ripgrep:
        return "orion-node"
    if os.environ.get("SSH_CONNECTION", "").strip():
        return "orion-node"
    if os.environ.get("DISPLAY", "").strip() == ":8":
        return ":8"
    cursor_data = os.environ.get("CURSOR_DATA_DIR", "")
    if "cursor-io" in cursor_data:
        return ":8"
    return None


def detect_cursor_gui_host(
    *,
    transcript_id: str | None = None,
    root_id: str | None = None,
    explicit_gui_host: str | None = None,
) -> str | None:
    """Resolve ``orion-node`` vs ``:8`` for wake subscription and CDP probes."""
    from cursor_bridge.cursor_agents_hop import is_cursor_xvfb_host

    from bus_watch.cursor_tab_whereami import resolve_cursor_tab_whereami

    if explicit_gui_host and is_cursor_xvfb_host(explicit_gui_host):
        return _normalize_gui_host(explicit_gui_host)
    out = resolve_cursor_tab_whereami(
        transcript_id=transcript_id,
        root_id=root_id,
        use_process_env=True,
    )
    host = out.get("gui_host")
    return str(host) if host else None


def _fetch_thread_slug(
    root_id: str, *, client: httpx.Client | None = None
) -> str | None:
    owns = client is None
    try:
        if owns:
            with _bus() as bus:
                row = _get(bus, f"/threads/{root_id}")
        else:
            row = _get(client, f"/threads/{root_id}")  # type: ignore[arg-type]
    except (Exception, SystemExit):
        return None
    if not row or row.get("_error"):
        return None
    slug = row.get("slug")
    return str(slug).strip() if slug else None


def resolve_subscription_title(
    root_id: str,
    *,
    slug: str | None = None,
    existing_title: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Preserve a non-empty title or build ``<root> <slug>`` when slug is known."""
    root = str(root_id).strip()
    existing = str(existing_title or "").strip()
    bare = f"{root} "
    if existing and existing not in {bare, root}:
        return existing
    slug_val = str(slug or "").strip() or _fetch_thread_slug(root, client=client)
    if slug_val:
        return f"{root} {slug_val}"
    return bare


def _policy_gui_host_pinned(policy: dict[str, Any]) -> bool:
    pinned = policy.get("gui_host_pinned")
    if pinned is True:
        return True
    if isinstance(pinned, str) and pinned.strip().lower() == "true":
        return True
    return False


def sync_policy_gui_host(
    root_id: str,
    gui_host: str,
    *,
    watch_dir: Path = WATCH_DIR,
) -> None:
    """Mirror process-detected ``gui_host`` into liaison tick policy when unpinned.

    Resume and liaison claim call this after ``detect_cursor_gui_host``; on
    cursor-server that detect value is often ``orion-node``, which would clobber
    an operator-pinned hop target (``:8``). When ``policy.gui_host_pinned`` is
    true, the stored ``gui_host`` is left unchanged. Endpoint wake routing still
    uses ``detect_cursor_gui_host`` so the live tab keeps its own display.
    """
    path = watch_dir / f"liaison-{root_id}.tick.json"

    def mutate(state: dict[str, Any]) -> None:
        policy = dict(state.get("policy") or {})
        if _policy_gui_host_pinned(policy):
            state["policy"] = policy
            return
        policy["gui_host"] = _normalize_gui_host(gui_host)
        state["policy"] = policy

    update_state(path, mutate)


__all__ = [
    "cursor_xvfb_display_for_gui_host",
    "cursor_xvfb_subscription_body",
    "detect_cursor_gui_host",
    "gui_host_from_process_env",
    "parse_ide_holder_agent_id",
    "resolve_subscription_title",
    "subscribe_cursor_xvfb_from_holder",
    "subscribe_cursor_xvfb_house",
    "sync_policy_gui_host",
    "unsubscribe_cursor_xvfb_house",
]
