"""Atomic single-mission Glass house birth for cursor_bridge ``spawn_house``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from bus_watch.cursor_xvfb_subscription import (
    cursor_xvfb_display_for_gui_host,
    subscribe_cursor_xvfb_house,
    unsubscribe_cursor_xvfb_house,
)
from bus_watch.fable_lock import WATCH_DIR
from bus_watch.tick_state import save_state

from cursor_bridge.cursor_agents_hop import (
    cursor_debug_url_for_gui_host,
    launch_cursor_agents_tab,
)
from cursor_bridge.spawn_model_knobs import merge_spawn_model_knobs


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _mint_root_thread(
    client: httpx.Client,
    *,
    slug: str,
    work_key: str,
) -> dict[str, Any]:
    body = (
        f"HOUSE-BIRTH {slug} work_key={work_key} — role:root continuity house on Glass."
    )
    payload = {
        "new_slug": slug,
        "from": "cursor",
        "to": "cursor",
        "subject": f"HOUSE {slug}",
        "body": body,
        "tags": ["role:root", "lane:liaison"],
    }
    try:
        resp = client.post("/threads/send", json=payload)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": "thread_mint_failed", "detail": str(exc)[:200]}
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": "thread_mint_http",
            "detail": f"http_{resp.status_code}",
            "body": resp.text[:300],
        }
    try:
        data = resp.json()
    except ValueError:
        return {"ok": False, "error": "thread_mint_non_json"}
    thread = data.get("thread") if isinstance(data.get("thread"), dict) else {}
    thread_id = str(thread.get("id") or "")
    if not thread_id:
        return {"ok": False, "error": "thread_mint_missing_id"}
    return {"ok": True, "thread_id": thread_id}


def _write_initial_tick(
    thread_id: str,
    *,
    gui_host: str,
    model_query: str,
    model_knobs: dict[str, str] | None,
    work_key: str,
    slug: str,
    sibling_roots: list[str] | None,
    watch_dir: Path = WATCH_DIR,
) -> Path:
    path = watch_dir / f"liaison-{thread_id}.tick.json"
    policy: dict[str, Any] = {
        "gui_host": gui_host,
        "hop_model_query": model_query,
        "single_mission_todo": work_key,
        "now_row": work_key,
    }
    if model_knobs:
        policy["hop_model_knobs"] = dict(model_knobs)
    if sibling_roots:
        policy["sibling_roots"] = list(sibling_roots)
    state = {
        "register": "autonomous",
        "born_at": _utcnow(),
        "policy": policy,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    save_state(path, state)
    return path


def spawn_house(
    *,
    slug: str,
    prompt: str,
    gui_host: str = ":8",
    model: str | None = None,
    effort: str | None = None,
    fast: bool | str | None = None,
    model_knobs: dict[str, str] | None = None,
    agent_id: str | None = None,
    work_key: str | None = None,
    sibling_roots: list[str] | None = None,
    watch_dir: Path = WATCH_DIR,
) -> dict[str, Any]:
    """Mint thread + tick policy + subscription, then launch Glass tab (rollback sub on CDP fail)."""
    slug_clean = str(slug or "").strip()
    prompt_clean = str(prompt or "").strip()
    if not slug_clean:
        return {
            "ok": False,
            "error": "slug_required",
            "detail": "spawn_house requires slug",
        }
    if not prompt_clean:
        return {
            "ok": False,
            "error": "prompt_required",
            "detail": "spawn_house requires prompt",
        }

    requested_agent_id = str(agent_id or "").strip() or str(uuid.uuid4())
    model_query = str(model or "").strip()
    knobs = merge_spawn_model_knobs(model_knobs, effort=effort, fast=fast)
    display = cursor_xvfb_display_for_gui_host(gui_host)
    debug_url = cursor_debug_url_for_gui_host(gui_host)

    from bus_watch.digest_budget import _bus

    mission_key = str(work_key or "").strip() or f"todo:{slug_clean}"
    with _bus() as client:
        mint = _mint_root_thread(client, slug=slug_clean, work_key=mission_key)
        if not mint.get("ok"):
            return mint
        thread_id = str(mint["thread_id"])

        tick_path = _write_initial_tick(
            thread_id,
            gui_host=gui_host,
            model_query=model_query,
            model_knobs=knobs,
            work_key=mission_key,
            slug=slug_clean,
            sibling_roots=sibling_roots,
            watch_dir=watch_dir,
        )

        title = f"{thread_id} {slug_clean}"
        sub = subscribe_cursor_xvfb_house(
            thread_id,
            requested_agent_id,
            display=display,
            title=title,
            bound_by="cursor-bridge-spawn-house",
            evidence="libs/cursor_bridge/spawn_house.spawn_house",
            client=client,
        )
        if not sub.get("ok"):
            return {
                "ok": False,
                "error": "subscription_failed",
                "detail": sub.get("reason") or sub.get("error"),
                "thread_id": thread_id,
                "agent_id": requested_agent_id,
                "tick_path": str(tick_path),
                "subscription": sub,
            }

        launch = launch_cursor_agents_tab(
            prompt_clean,
            model_query=model_query,
            model_knobs=knobs,
            debug_url=debug_url,
            requested_agent_id=requested_agent_id,
            gui_host=gui_host,
        )
        if not launch.get("ok"):
            rollback = unsubscribe_cursor_xvfb_house(
                thread_id,
                requested_agent_id,
                client=client,
                bound_by="cursor-bridge-spawn-house-rollback",
                evidence="spawn_house CDP launch !ok",
            )
            return {
                "ok": False,
                "error": "launch_failed",
                "detail": launch.get("reason") or launch.get("error"),
                "thread_id": thread_id,
                "agent_id": requested_agent_id,
                "gui_host": gui_host,
                "subscription_rollback": rollback,
                "launch": launch,
            }

        return {
            "ok": True,
            "thread_id": thread_id,
            "agent_id": requested_agent_id,
            "gui_host": gui_host,
            "display": display,
            "subscription": sub,
            "tick_path": str(tick_path),
            "launch": launch,
            "title": title,
        }


__all__ = ["spawn_house"]
