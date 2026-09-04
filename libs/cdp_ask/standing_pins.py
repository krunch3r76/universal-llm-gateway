"""Standing CDP pin health projection for cdp-ask /health.

Rebuilds display and standing-pin state from systemd + CDP probes on each
health request. Advisory only — not journal-backed authority.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from cdp_ask.events.standing_lane import (
    emit_standing_down,
    emit_standing_lapsed,
    emit_standing_up,
)

logger = logging.getLogger(__name__)

StandingState = str  # DOWN | UP | LAPSED


@dataclass(frozen=True)
class StandingPinHealth:
    state: StandingState
    source: str
    observed_at: str


def _pins_path() -> Path:
    repo = os.environ.get("ULG_REPO", "").strip()
    if not repo:
        raise RuntimeError("ULG_REPO not set")
    return Path(repo) / "services" / "jupiter-cdp" / "pins.toml"


def _load_pins() -> dict[str, dict]:
    with _pins_path().open("rb") as f:
        return tomllib.load(f).get("lanes", {})


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _unit_active(lane: str) -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", f"cdp-lane@{lane}.service"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        return False
    return proc.stdout.strip() == "active"


def _cdp_json(port: int) -> dict | None:
    url = f"http://127.0.0.1:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=0.5) as resp:
            if resp.status != 200:
                return None
            import json

            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _top_page_url(port: int) -> str | None:
    list_url = f"http://127.0.0.1:{port}/json/list"
    try:
        with urllib.request.urlopen(list_url, timeout=0.5) as resp:
            import json

            pages = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(pages, list):
        return None
    for page in pages:
        if isinstance(page, dict) and page.get("type") == "page" and page.get("url"):
            return str(page["url"])
    return None


def _lapsed_match(url: str | None, prefixes: list[str]) -> str | None:
    if not url or not prefixes:
        return None
    path = urlparse(url).path
    for prefix in prefixes:
        if path.startswith(prefix):
            return prefix
    return None


def _emit_transition(
    state: StandingState,
    *,
    name: str,
    port: int,
    observed_at: str,
    url_prefix: str | None = None,
) -> None:
    """Emit standing transition; never raise into /health projection."""
    try:
        if state == "DOWN":
            emit_standing_down(lane=name, port=port, observed_at=observed_at)
        elif state == "LAPSED":
            emit_standing_lapsed(
                lane=name,
                port=port,
                observed_at=observed_at,
                url_prefix=url_prefix or "",
            )
        else:
            emit_standing_up(lane=name, port=port, observed_at=observed_at)
    except Exception:
        logger.warning(
            "standing_pins emit failed lane=%s state=%s", name, state, exc_info=True
        )


def _probe_lane(
    name: str, row: dict, *, prev: StandingState | None
) -> StandingPinHealth:
    observed_at = _iso_now()
    port = int(row["port"])
    prefixes = list(row.get("lapsed_url_prefixes") or [])

    if not _unit_active(name) or _cdp_json(port) is None:
        state: StandingState = "DOWN"
        health = StandingPinHealth(
            state=state, source="systemd+cdp", observed_at=observed_at
        )
        if prev != state:
            _emit_transition(state, name=name, port=port, observed_at=observed_at)
        return health

    matched = _lapsed_match(_top_page_url(port), prefixes)
    if matched is not None:
        state = "LAPSED"
        health = StandingPinHealth(
            state=state, source="systemd+cdp", observed_at=observed_at
        )
        if prev != state:
            _emit_transition(
                state,
                name=name,
                port=port,
                observed_at=observed_at,
                url_prefix=matched,
            )
        return health

    state = "UP"
    health = StandingPinHealth(
        state=state, source="systemd+cdp", observed_at=observed_at
    )
    if prev != state:
        _emit_transition(state, name=name, port=port, observed_at=observed_at)
    return health


def _probe_display(display: str) -> str:
    """Return ``live`` | ``unauthorized`` | ``dead`` | ``hung`` for /health (a:32225)."""
    from claude_bundles.cdp_display_auth import (
        DisplayAuthError,
        apply_display_auth_env,
        resolve_display_auth,
        xdpyinfo_status,
    )

    env = {**os.environ, "DISPLAY": display}
    try:
        auth = resolve_display_auth(display)
        apply_display_auth_env(env, display, auth=auth)
    except DisplayAuthError:
        env.pop("XAUTHORITY", None)
    status, _snippet = xdpyinfo_status(display, env=env)
    return status


_prev_states: dict[str, StandingState] = {}


def probe_health() -> tuple[dict[str, str], dict[str, StandingPinHealth]]:
    """Return ``(displays, standing_pins)`` for /health."""
    displays = {":2": _probe_display(":2"), ":3": _probe_display(":3")}
    standing: dict[str, StandingPinHealth] = {}
    try:
        pins = _load_pins()
        for name, row in pins.items():
            if not row.get("standing"):
                continue
            prev = _prev_states.get(name)
            health = _probe_lane(name, row, prev=prev)
            _prev_states[name] = health.state
            standing[name] = health
    except Exception:
        logger.warning("standing_pins probe_health failed", exc_info=True)
    return displays, standing
