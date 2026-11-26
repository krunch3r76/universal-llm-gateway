"""Standing-house CDP snapshot verification (V1 draft, stage-1b text)."""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import tomllib
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from browser_governance import events as _events
from browser_governance.contract import STANDING_HOUSES

_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _default_pins_path() -> Path | None:
    repo = os.environ.get("ULG_REPO", "").strip()
    if not repo:
        return None
    return Path(repo) / "services" / "jupiter-cdp" / "pins.toml"


def _standing_pin_profiles(pins_path: Path | None) -> dict[int, str]:
    if pins_path is None or not pins_path.is_file():
        return {}
    try:
        with pins_path.open("rb") as f:
            lanes = tomllib.load(f).get("lanes", {})
    except OSError:
        return {}
    out: dict[int, str] = {}
    for lane in lanes.values():
        if not isinstance(lane, dict) or not lane.get("standing"):
            continue
        try:
            port = int(lane["port"])
        except (KeyError, TypeError, ValueError):
            continue
        profile = lane.get("profile")
        if profile:
            out[port] = str(profile)
    return out


def standing_house_catalog(
    *,
    host: str | None = None,
    pins_path: Path | str | None = None,
    extra_houses: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build catalog rows from ``STANDING_HOUSES`` for one host."""
    if extra_houses is not None:
        return list(extra_houses)

    host_key = (host or socket.gethostname().split(".", 1)[0]).strip().lower()
    path = (
        Path(pins_path)
        if pins_path is not None
        else _default_pins_path()
    )
    pin_profiles = _standing_pin_profiles(path)

    rows: list[dict[str, Any]] = []
    for owner_id in STANDING_HOUSES.values():
        parts = str(owner_id).split(":")
        if len(parts) < 3:
            continue
        row_host = parts[0].lower()
        if row_host != host_key:
            continue
        try:
            port = int(parts[-1])
        except ValueError:
            continue
        rows.append(
            {
                "owner_id": owner_id,
                "cdp_port": port,
                "profile": pin_profiles.get(port),
            }
        )
    return rows


def _cdp_json_list(port: int) -> list[dict[str, Any]]:
    """GET the CDP target list of a local debug port; an unreachable port reads as no targets."""
    try:
        with _LOCAL_OPENER.open(f"http://127.0.0.1:{port}/json/list", timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError, http.client.HTTPException):
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict)]


def verify_seats(
    *,
    cdp_fetch: Callable[[int], list[dict[str, Any]]] | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Observe each catalog house once: CDP reachability, page targets, profile presence."""
    rows = catalog if catalog is not None else standing_house_catalog()
    fetch = cdp_fetch or _cdp_json_list
    out: list[dict[str, Any]] = []
    for house in rows:
        port = int(house["cdp_port"])
        targets = fetch(port)
        profile = house.get("profile")
        out.append(
            {
                "owner_id": house.get("owner_id"),
                "cdp_port": port,
                "reachable": bool(targets),
                "page_targets": sum(1 for t in targets if t.get("type") == "page"),
                "profile_present": Path(str(profile)).expanduser().is_dir() if profile else None,
            }
        )
    return out


def _seat_ok(row: dict[str, Any]) -> bool:
    return (
        bool(row.get("reachable"))
        and bool(row.get("page_targets"))
        and row.get("profile_present") is not False
    )


def seat_snapshot_diff(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Name each house that was a working seat in *before* and is not one in *after."""
    after_by_owner = {row.get("owner_id"): row for row in after}
    diffs: list[dict[str, Any]] = []
    for prev in before:
        if not _seat_ok(prev):
            continue
        owner = prev.get("owner_id")
        now = after_by_owner.get(owner)
        if now is None or not _seat_ok(now):
            diffs.append({"owner_id": owner, "lost": True, "before": prev, "after": now})
    with contextlib.suppress(Exception):
        _events.emit_browser_event(
            _events.browser_seat_verification(
                diff_count=len(diffs),
                houses_checked=len(before),
                lost_owner_ids=[str(d["owner_id"]) for d in diffs],
            )
        )
    return diffs
