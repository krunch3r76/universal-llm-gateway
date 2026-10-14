"""Mint or reuse the ``{root}-loop-tape`` hop child and bind ``policy.loop_thread``.

Tier-1 policy guard (zero bus when set) → tier-2 lineage slug reuse → tier-3
``POST /threads/send`` mint. Non-fatal on failure — callers proceed without
``loop_thread`` until a later birth or ``--go-under`` retry.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from bus_watch.digest_budget import _bus, _get, effective_policy
from bus_watch.tick_state import update_state

SendFn = Callable[..., dict[str, Any] | None]


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _target_slug(root: str) -> str:
    return f"{root}-loop-tape"


def _log_minted(root: str, child: str, *, reused: bool) -> None:
    print(
        json.dumps(
            {
                "loop": "loop_tape_minted",
                "root": root,
                "child": child,
                "reused": reused,
            }
        ),
        flush=True,
    )


def _log_failed(root: str, error: str) -> None:
    print(
        json.dumps(
            {"loop": "loop_tape_mint_failed", "root": root, "error": error[:200]}
        ),
        flush=True,
    )


def _find_reusable_child(client: httpx.Client, root: str) -> str | None:
    """Reuse a hop child whose slug matches ``{root}-loop-tape`` exactly."""
    target = _target_slug(root)
    lin = _get(client, f"/threads/{root}/lineage") or {}
    for child in lin.get("children") or []:
        if not isinstance(child, dict):
            continue
        if child.get("lane_role") != "hop":
            continue
        tid = str(child.get("thread_id") or "")
        if not tid:
            continue
        detail = _get(client, f"/threads/{tid}")
        if not detail or "_error" in detail:
            continue
        if detail.get("slug") == target:
            return tid
    return None


def _mint_child(
    client: httpx.Client,
    root: str,
    state: dict[str, Any],
) -> str | None:
    slug = _target_slug(root)
    born_at = str(state.get("born_at") or _utcnow())
    body = (
        f"LOOP-TAPE {root} born_at={born_at} — machine posts "
        f"(DIGEST, ORIENTED, navigator wake) land here."
    )
    payload = {
        "new_slug": slug,
        "parent_thread": root,
        "lane_role": "hop",
        "from": "cursor",
        "to": "web-anthropic",
        "subject": f"LOOP-TAPE {root}",
        "body": body,
    }
    try:
        resp = client.post("/threads/send", json=payload)
    except httpx.HTTPError as exc:
        _log_failed(root, f"{type(exc).__name__}: {exc}")
        return None
    if resp.status_code >= 400:
        _log_failed(root, f"http_{resp.status_code}")
        return None
    try:
        data = resp.json()
    except ValueError:
        _log_failed(root, "non_json_response")
        return None
    thread = data.get("thread") if isinstance(data.get("thread"), dict) else {}
    child = str(thread.get("id") or "")
    if not child:
        _log_failed(root, "missing_thread_id")
        return None
    return child


def _persist_loop_thread(state_path: Path, child: str) -> None:
    def _mutate(st: dict[str, Any]) -> None:
        policy = dict(st.get("policy") or {})
        policy["loop_thread"] = child
        st["policy"] = policy

    update_state(state_path, _mutate)


def ensure_loop_tape(
    root: str,
    state: dict[str, Any],
    state_path: Path,
    *,
    send: SendFn | None = None,
) -> str | None:
    """Ensure ``policy.loop_thread`` names the ``{root}-loop-tape`` hop child."""
    existing = str(effective_policy(state).get("loop_thread") or "").strip()
    if existing:
        return existing

    def _resolve(client: httpx.Client) -> tuple[str | None, bool]:
        reused = _find_reusable_child(client, root)
        if reused:
            return reused, True
        minted = _mint_child(client, root, state)
        return minted, False

    child: str | None
    reused: bool
    if send is not None:
        result = send(root=root, state=state, state_path=state_path)
        if result is None:
            return None
        child = str(result.get("child") or result.get("loop_thread") or "")
        reused = bool(result.get("reused"))
        if not child:
            _log_failed(root, "send_callback_empty")
            return None
    else:
        try:
            with _bus() as client:
                child, reused = _resolve(client)
        except SystemExit:
            _log_failed(root, "bus_unavailable")
            return None
        except OSError as exc:
            _log_failed(root, f"{type(exc).__name__}: {exc}")
            return None

    if not child:
        return None

    _persist_loop_thread(state_path, child)
    _log_minted(root, child, reused=reused)
    return child


__all__ = ["ensure_loop_tape"]
