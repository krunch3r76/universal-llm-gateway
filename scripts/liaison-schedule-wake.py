#!/usr/bin/env python3
"""Schedule a recurring liaison WAKE doorbell via GIW ``/api/v1/triggers``.

Uses ``recur_every_s`` (default 14400 s = 4 h) — not cron ``0 */4 * * *``;
each fire re-arms from the prior terminal seam, so wall-clock drift is expected.
GIW refuses recur without ``predicate=fleet_idle`` (``recur_every_s_invalid``);
this CLI always sends that predicate when recur is set.

Cowork ``create_trigger`` is not used; this posts to the same store as MCP
``trigger(op=schedule)``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml
from bus_watch.doorbell import doorbell_prompt_uri, ensure_doorbell_file
from implement_admission.closeout_helpers import cortex_files_root

_DEFAULT_WORKER_URL = "http://127.0.0.1:8091"
_API_PREFIX = "/api/v1/triggers"
_DEFAULT_RECUR_S = 14400
_ACTIVE_STATUSES = frozenset({"scheduled", "fired"})
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
# GIW validate_predicate_schedule: recur_every_s without fleet_idle → 422
# recur_every_s_invalid. Args match trigger_service tests' _FLEET_ARGS.
_FLEET_IDLE_ARGS = {
    "require_tick_empty": True,
    "require_dispatch_idle": True,
    "grace_s": 0,
}


def _worker_base_url() -> str:
    explicit = os.environ.get("GIT_INTEGRATION_WORKER_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    stargate = os.environ.get("STARGATE_URL", "").strip()
    if stargate:
        return stargate.rstrip("/")
    return _DEFAULT_WORKER_URL


def _bearer_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if not token and _MCP_YAML.is_file():
        cfg = yaml.safe_load(_MCP_YAML.read_text(encoding="utf-8")) or {}
        token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _relay(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    url = f"{_worker_base_url()}{_API_PREFIX}{path}"
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.request(
            method,
            url,
            json=json_body,
            params=params,
            headers=_bearer_headers(),
        )
        if resp.content:
            body = resp.json()
        else:
            body = {"ok": True}
        if resp.status_code >= 400:
            if isinstance(body, dict):
                body.setdefault("status_code", resp.status_code)
            return body
        return body


def _ensure_doorbell_file(
    root: str,
    *,
    slug: str | None,
    slug_explicit: bool,
    ring: str | None,
    ring_explicit: bool,
) -> str:
    uri = doorbell_prompt_uri(root)
    rel = uri.removeprefix("cortex://").lstrip("/")
    path = (cortex_files_root() / rel).resolve()
    root_resolved = cortex_files_root().resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"doorbell path escapes CORTEX_FILES_ROOT: {uri}") from exc
    ensure_doorbell_file(
        path,
        root=root,
        slug=slug,
        slug_explicit=slug_explicit,
        ring=ring,
        ring_explicit=ring_explicit,
    )
    return uri


def _find_active_liaison_wake(root: str) -> dict[str, Any] | None:
    so_what = f"liaison-wake-{root}"
    listed = _relay("GET", "", params={"limit": 200})
    if "error" in listed:
        return None
    for row in listed.get("triggers") or []:
        if row.get("so_what") != so_what:
            continue
        if row.get("status") in _ACTIVE_STATUSES:
            return row
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--root", required=True, help="Continuity root thread id (e.g. 10479)"
    )
    p.add_argument(
        "--slug",
        default=argparse.SUPPRESS,
        help="Doorbell slug passed to render_doorbell (default: liaison-wake for new files)",
    )
    p.add_argument(
        "--ring",
        default=argparse.SUPPRESS,
        help="Echo thread for ORIENTED (default: root for new files)",
    )
    p.add_argument(
        "--delay-s",
        type=float,
        default=None,
        help="Seconds until first fire (default: immediate-ish 5 s if unset)",
    )
    p.add_argument(
        "--recur-s",
        type=int,
        default=_DEFAULT_RECUR_S,
        help=f"recur_every_s for GIW store (default: {_DEFAULT_RECUR_S})",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Schedule even when an active liaison-wake row exists for this root",
    )
    args = p.parse_args(argv)

    root = str(args.root).strip()
    if not root:
        print("error: --root required", file=sys.stderr)
        return 2

    existing = _find_active_liaison_wake(root)
    if existing and not args.force:
        print(
            json.dumps(
                {
                    "skipped": True,
                    "reason": "active_liaison_wake_exists",
                    "trigger_id": existing.get("id"),
                    "status": existing.get("status"),
                    "so_what": existing.get("so_what"),
                },
                indent=2,
            )
        )
        return 0

    slug_explicit = hasattr(args, "slug")
    ring_explicit = hasattr(args, "ring")
    slug = getattr(args, "slug", None)
    ring = getattr(args, "ring", None)

    prompt_uri = _ensure_doorbell_file(
        root,
        slug=slug,
        slug_explicit=slug_explicit,
        ring=ring,
        ring_explicit=ring_explicit,
    )
    delay_s = args.delay_s if args.delay_s is not None else 5.0
    body: dict[str, Any] = {
        "created_by": "life-seat",
        "delay_s": delay_s,
        "prompt_uri": prompt_uri,
        "purpose": "ask",
        "model": "opus-5",
        "arc": f"agent-bus:{root}",
        "so_what": f"liaison-wake-{root}",
        "recur_every_s": args.recur_s,
    }
    if args.recur_s:
        body["predicate"] = "fleet_idle"
        body["predicate_args"] = dict(_FLEET_IDLE_ARGS)
    result = _relay("POST", "", json_body=body)
    if "error" in result or result.get("status_code", 0) >= 400:
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 1

    trigger_id = result.get("id") or result.get("trigger_id")
    fire_at = result.get("fire_at")
    print(
        json.dumps(
            {
                "trigger_id": trigger_id,
                "fire_at": fire_at,
                "recur_every_s": args.recur_s,
                "predicate": body.get("predicate"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
