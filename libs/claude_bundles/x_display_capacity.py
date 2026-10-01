"""X11/Xvfb client occupancy for CDP Chrome mint — a distinct capacity axis.

``free_slots`` on active-work counts recorded project-ask *streams* against
the advisory ceilings in ``cdp_ask.lane_admission``. Xvfb ``-maxclients``
counts unix connections on the display. A
registry that still has a TCP port, and a satellite that still has stream
slots, can both report room while the display cannot host another multiprocess
Chrome. This module is the X axis: probe ``/proc/net/unix`` the same way the
9498 live incident did, refuse mint when headroom is below one Chrome budget,
and publish the scalars next to ``free_slots`` without rewriting that formula.

Who calls: ``register_lane`` / ``relaunch_dormant`` / ``_allocate_port_for_profile``
before they pick a port, and ``_launch_chrome`` again on listen-timeout so a
TOCTOU miss still names X instead of blaming the browser.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from admission_common.qualified_scalar import (
    AuthorityClass,
    QualifiedScalar,
    SurfaceDecl,
)

X_MAX_CLIENTS_DEFAULT = 64
CHROME_X_CLIENT_BUDGET_DEFAULT = 8
X_MAX_CLIENTS_TOKEN = "Maximum number of clients reached"
# Chrome X-auth failures that previously surfaced only as a 20s CDP listen timeout (a:32225).
# "The platform failed to initialize" is ozone/GPU-broad — only counts with an X token (Opus F6).
X_DISPLAY_DEAD_TOKENS = (
    "Missing X server or $DISPLAY",
    "Authorization required, but no authorization protocol specified",
)
_PROC_NET_UNIX = Path("/proc/net/unix")
_X_CLIENTS_SCOPE = (
    "Xvfb/X11 unix connections on CDP_DISPLAY (X11-unix/Xn lines in /proc/net/unix)"
)
_X_HEADROOM_SCOPE = (
    "x_max_clients minus x_clients; None when the unix table was unreadable"
)
_X_EXHAUSTED_SCOPE = (
    "True when observed headroom is below one multiprocess Chrome budget; "
    "None when x_clients is unobserved"
)
_X_MAX_SCOPE = (
    "X MaxClients belief: live Xvfb -maxclients when readable, else "
    "CDP_X_MAX_CLIENTS, else default 64; min(live, env) when both are set"
)
_X_BUDGET_SCOPE = "unix clients reserved for one multiprocess Chrome (CDP_X_CHROME_CLIENT_BUDGET, default 8)"


class XDisplayCapacityError(RuntimeError):
    """Raised when Xvfb/X11 MaxClients cannot host another multiprocess Chrome.

    Callers treat this as a mint admission refusal, not a browser hang: the
    message names the display and client counts instead of a CDP listen timeout.
    """


def chrome_cdp_log_path(port: int) -> str:
    """Return the stderr/stdout log path ``_launch_chrome`` appends for *port*.

    Shared so listen-timeout scrape and the Popen redirect use the same file.
    """
    return f"/tmp/chrome-cdp-claude-ai-{port}.log"


def display_x11_socket_name(display: str) -> str:
    """Map ``:2`` / ``:2.0`` / ``2`` to the ``X11-unix/X2`` basename token."""
    from claude_bundles.cdp_display_auth import display_digit

    return f"X{display_digit(display)}"


def count_x11_unix_clients(
    display: str,
    *,
    proc_net_unix: Path | None = None,
) -> int | None:
    """Count ``/proc/net/unix`` lines bound to this display's X11 socket.

    Returns None when the table cannot be read so callers fail *open* on a
    missing procfs rather than inventing exhaustion. The count includes the
    listening socket, matching the 9498 Jupiter probe (63 lines ≅ 63 fds).
    """
    path = proc_net_unix if proc_net_unix is not None else _PROC_NET_UNIX
    token = f"X11-unix/{display_x11_socket_name(display)}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return sum(1 for line in text.splitlines() if token in line)


def _cdp_display() -> str:
    from claude_bundles.cdp_lane import cdp_display

    return cdp_display()


_MAXCLIENTS_CACHE_TTL_S = 5.0
# display -> (id(reader), monotonic, parsed maxclients or None)
_MAXCLIENTS_CACHE: dict[str, tuple[int, float, int | None]] = {}


def _proc_cmdlines(proc_root: Path | None = None) -> list[list[str]] | None:
    """Argv lists under ``/proc/<pid>/cmdline``. None when procfs cannot be listed.

    A single unreadable pid is skipped. Tests monkeypatch this the same way
    ``count_x11_unix_clients`` takes an injected unix table.
    """
    root = proc_root if proc_root is not None else Path("/proc")
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    found: list[list[str]] = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        parts = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if parts:
            found.append(parts)
    return found


def _argv_maxclients(argv: list[str]) -> int | None:
    for index, part in enumerate(argv):
        if part != "-maxclients" or index + 1 >= len(argv):
            continue
        with contextlib.suppress(ValueError):
            value = int(argv[index + 1])
            if value > 0:
                return value
        return None
    return None


def _maxclients_in_cmdlines(cmdlines: list[list[str]] | None, display: str) -> int | None:
    """Parse ``-maxclients`` from an ``Xvfb :<N>`` argv. None if absent or unreadable."""
    if cmdlines is None:
        return None
    from claude_bundles.cdp_lane import _display_key

    try:
        want = _display_key(display)
    except (TypeError, ValueError):
        return None
    for argv in cmdlines:
        if not any(Path(part).name == "Xvfb" for part in argv):
            continue
        shown = next(
            (part for part in argv if part.startswith(":") and len(part) > 1),
            None,
        )
        if shown is None:
            continue
        try:
            if _display_key(shown) != want:
                continue
        except (TypeError, ValueError):
            continue
        return _argv_maxclients(argv)
    return None


def _live_maxclients(display: str) -> int | None:
    """Live Xvfb ``-maxclients`` for *display*, cached a few seconds per display."""
    reader = _proc_cmdlines
    now = time.monotonic()
    hit = _MAXCLIENTS_CACHE.get(display)
    if hit is not None and hit[0] == id(reader) and now - hit[1] < _MAXCLIENTS_CACHE_TTL_S:
        return hit[2]
    try:
        cmdlines = reader()
    except OSError:
        cmdlines = None
    value = _maxclients_in_cmdlines(cmdlines, display)
    _MAXCLIENTS_CACHE[display] = (id(reader), now, value)
    return value


def _env_maxclients() -> int | None:
    raw = os.environ.get("CDP_X_MAX_CLIENTS", "").strip()
    if not raw:
        return None
    with contextlib.suppress(ValueError):
        value = int(raw)
        if value > 0:
            return value
    return None


def _max_clients(display: str) -> int:
    """Belief for *display*: min(live, env) when both exist, else live, else env, else 64.

    Unreadable procfs falls through to the env / default. Does not raise.
    """
    live = _live_maxclients(display)
    env = _env_maxclients()
    if live is not None and env is not None:
        return min(live, env)
    if live is not None:
        return live
    if env is not None:
        return env
    return X_MAX_CLIENTS_DEFAULT


def _load_pin_lanes() -> dict[str, dict]:
    """Lanes from ``pins.toml`` via ``standing_pins._load_pins``.

    Missing path, unset ``ULG_REPO``, or malformed TOML yields ``{}`` so mint
    does not raise. Callers then reserve nothing and do not hide pin ports.
    """
    try:
        from cdp_ask.standing_pins import _load_pins

        loaded = _load_pins()
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(key): row for key, row in loaded.items() if isinstance(row, dict)}


def pin_lanes_on_display(display: str, lanes: Mapping[str, Any] | None = None) -> int:
    """Pin lanes whose ``display`` matches *display*. One Chrome budget each."""
    from claude_bundles.cdp_lane import _display_key

    rows = _load_pin_lanes() if lanes is None else lanes
    try:
        want = _display_key(display)
    except (TypeError, ValueError):
        return 0
    count = 0
    for row in rows.values():
        if not isinstance(row, dict):
            continue
        raw = row.get("display")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            if _display_key(raw) == want:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


def pin_ports(lanes: Mapping[str, Any] | None = None) -> set[int]:
    """TCP ports named in ``pins.toml``. Empty when the file cannot be read."""
    rows = _load_pin_lanes() if lanes is None else lanes
    ports: set[int] = set()
    for row in rows.values():
        if not isinstance(row, dict):
            continue
        port = row.get("port")
        if isinstance(port, int) and not isinstance(port, bool):
            ports.add(port)
    return ports


def _chrome_budget() -> int:
    raw = os.environ.get("CDP_X_CHROME_CLIENT_BUDGET", "").strip()
    if raw:
        with contextlib.suppress(ValueError):
            value = int(raw)
            if value > 0:
                return value
    return CHROME_X_CLIENT_BUDGET_DEFAULT


def probe_x_display(
    *,
    display: str | None = None,
    count: int | None = None,
    max_clients: int | None = None,
    chrome_budget: int | None = None,
    proc_net_unix: Path | None = None,
) -> dict[str, Any]:
    """Snapshot X occupancy for mint admission and the active-work wire.

    Pass *count* to inject a unix-table reading in tests; omit it to read
    ``/proc/net/unix``. Unreadable procfs yields ``x_exhausted=None`` (unobserved).
    """
    resolved_display = display if display is not None else _cdp_display()
    cap = _max_clients(resolved_display) if max_clients is None else max_clients
    budget = _chrome_budget() if chrome_budget is None else chrome_budget
    if count is None:
        observed = count_x11_unix_clients(resolved_display, proc_net_unix=proc_net_unix)
        probe = "proc_net_unix" if observed is not None else "unavailable"
    else:
        observed = count
        probe = "injected"
    if observed is None:
        headroom: int | None = None
        exhausted: bool | None = None
    else:
        headroom = max(0, cap - observed)
        exhausted = headroom < budget
    return {
        "x_display": resolved_display,
        "x_clients": observed,
        "x_max_clients": cap,
        "x_headroom": headroom,
        "x_exhausted": exhausted,
        "x_chrome_client_budget": budget,
        "x_probe": probe,
    }


def exhausted_message(snap: Mapping[str, Any]) -> str:
    """Build the operator-facing refusal that names X occupancy, not Chrome/CDP.

    Used both at pre-port-select refuse and as the body of ``XDisplayCapacityError``.
    """
    display = snap.get("x_display") or ":?"
    clients = snap.get("x_clients")
    cap = snap.get("x_max_clients")
    budget = snap.get("x_chrome_client_budget")
    return (
        f"X display {display} exhausted: {clients} of {cap} clients "
        f"(need {budget} free for one multiprocess Chrome); "
        f"refusing mint rather than waiting for Chrome CDP"
    )


def _log_chunk(log_path: str, *, start_offset: int = 0) -> bytes:
    """Return append-only log bytes for *this* launch (after ``start_offset``)."""
    path = Path(log_path)
    try:
        data = path.read_bytes()
    except OSError:
        return b""
    return data[max(0, start_offset) :]


def log_bytes_show_x_exhaustion(log_path: str, *, start_offset: int = 0) -> bool:
    """True when *this launch's* appended log bytes contain the MaxClients token.

    The Chrome log is opened append-only, so a prior failed mint would otherwise
    poison every later timeout. Only bytes after ``start_offset`` count.
    """
    return X_MAX_CLIENTS_TOKEN.encode("utf-8") in _log_chunk(
        log_path, start_offset=start_offset
    )


def log_bytes_show_display_dead(log_path: str, *, start_offset: int = 0) -> bool:
    """True when *this launch's* log shows X auth / missing-display failure (a:32225).

    Ozone's ``The platform failed to initialize`` alone is not X-auth evidence —
    it must co-occur with a genuine X token (Opus F6).
    """
    chunk = _log_chunk(log_path, start_offset=start_offset)
    has_x = any(token.encode("utf-8") in chunk for token in X_DISPLAY_DEAD_TOKENS)
    if not has_x:
        return False
    return True


def listen_timeout_x_message(port: int, log_path: str) -> str:
    """Replace the 20s Chrome-timeout string when the log named X."""
    return (
        f"Chrome on :{port} did not reach CDP because X display reported "
        f"{X_MAX_CLIENTS_TOKEN!r} in {log_path} "
        f"(not a browser hang)"
    )


def listen_timeout_display_dead_message(port: int, log_path: str, display: str) -> str:
    """Replace the generic listen timeout when Chrome could not open the X display."""
    return (
        f"Chrome on :{port} did not reach CDP because display {display} is "
        f"unreachable or unauthorized (see {log_path}); "
        f"expected XAUTHORITY at per-display path under ~/.gateway/cdp-xvfb/"
    )


def require_cdp_display_reachable(
    display: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Fail fast when the *launch* env cannot open CDP_DISPLAY (a:32225).

    Prefer passing the exact ``env`` dict that will be handed to Chrome so the
    gate and the mint cannot diverge (Opus F2). When *env* is omitted, builds
    one via ``chrome_display_env`` (single resolver).
    """
    from claude_bundles.cdp_display_auth import (
        DisplayAuthError,
        require_auth_authenticates,
    )
    from claude_bundles.cdp_lane import cdp_display, chrome_display_env

    display_val = (
        str(env.get("DISPLAY") or env.get("CDP_DISPLAY") or "").strip()
        if env is not None
        else ""
    ) or cdp_display(display)
    try:
        run_env = dict(env) if env is not None else chrome_display_env(display_val)
        require_auth_authenticates(display_val, env=run_env)
    except DisplayAuthError as exc:
        raise XDisplayCapacityError(str(exc)) from exc
    return display_val


def _display_auth_resolves(display: str) -> bool:
    """True when per-display → flat → live Xvfb ``-auth`` yields a cookie path."""
    from claude_bundles.cdp_display_auth import DisplayAuthError, resolve_display_auth

    try:
        resolved = resolve_display_auth(display)
    except DisplayAuthError:
        return False
    return resolved.path is not None


def _reserved_on_display(display: str, reserved_by_display: Mapping[str, int]) -> int:
    from claude_bundles.cdp_lane import _display_key

    key = _display_key(display)
    for raw, count in reserved_by_display.items():
        try:
            if _display_key(str(raw)) != key:
                continue
            return count if count > 0 else 0
        except (TypeError, ValueError):
            continue
    return 0


def _emit_display_fallover(
    *,
    admitted: str,
    skipped: list[str],
    candidates: list[str],
) -> None:
    with contextlib.suppress(Exception):
        from claude_bundles import cdp_registry_events as _events

        _events.emit(
            _events.cdp_port_display_fallover(
                admitted=admitted,
                skipped=skipped,
                candidates=candidates,
            )
        )


def admit_display(
    candidates: list[str],
    reserved_by_display: Mapping[str, int],
    *,
    counts: Mapping[str, int] | None = None,
    max_clients: int | None = None,
    chrome_budget: int | None = None,
    proc_net_unix: Path | None = None,
    auth_resolves: Callable[[str], bool] | None = None,
) -> str:
    """Return the first candidate with headroom and a resolvable XAUTHORITY.

    Sticky order, not least-loaded. Raises ``XDisplayCapacityError`` naming
    every candidate's counts when none admits.

    A sole candidate that has headroom but no resolvable XAUTHORITY is still
    returned. Single-display hosts then fail inside ``_launch_chrome`` via
    ``require_cdp_display_reachable``, which is today's refusal. With another
    candidate left, missing auth skips this display.
    """
    from claude_bundles.cdp_lane import _display_key

    normalized: list[str] = []
    for raw in candidates:
        item = str(raw).strip()
        if not item:
            continue
        if not item.startswith(":"):
            item = f":{item}"
        key = _display_key(item)
        if key not in normalized:
            normalized.append(key)
    if not normalized:
        raise XDisplayCapacityError("X display fallover exhausted: no candidates")

    failures: list[str] = []
    skipped: list[str] = []
    for index, display in enumerate(normalized):
        reserved = _reserved_on_display(display, reserved_by_display)
        count = counts.get(display) if counts is not None else None
        try:
            snap = require_chrome_headroom(
                display=display,
                count=count,
                max_clients=max_clients,
                chrome_budget=chrome_budget,
                proc_net_unix=proc_net_unix,
                reserved_chromes=reserved,
            )
        except XDisplayCapacityError as exc:
            failures.append(str(exc))
            skipped.append(display)
            continue
        resolves = (
            bool(auth_resolves(display))
            if auth_resolves is not None
            else _display_auth_resolves(display)
        )
        if resolves:
            if skipped:
                _emit_display_fallover(
                    admitted=display, skipped=skipped, candidates=normalized
                )
            return display
        if index == len(normalized) - 1 and not skipped:
            # Sole candidate: defer auth to launch (single-display today).
            return display
        clients = snap.get("x_clients")
        cap = snap.get("x_max_clients")
        failures.append(
            f"X display {display}: {clients} of {cap} clients, no resolvable XAUTHORITY"
        )
        skipped.append(display)
    raise XDisplayCapacityError("X display fallover exhausted: " + "; ".join(failures))


def require_chrome_headroom(
    *,
    display: str | None = None,
    count: int | None = None,
    max_clients: int | None = None,
    chrome_budget: int | None = None,
    proc_net_unix: Path | None = None,
    reserved_chromes: int = 0,
    after_drain: bool = False,
) -> dict[str, Any]:
    """Refuse mint when observed X headroom cannot host one more Chrome.

    *reserved_chromes* counts rows already ``allocating`` so two concurrent
    mints cannot both pass a check that only saw the current process table.
    Pin lanes on this display (``pins.toml``) add one Chrome budget each on
    top of that, so a mint cannot spend the pins' own room. Values below 0
    are treated as 0.

    Refuse when ``headroom - (reserved_chromes + pin_lanes) * budget < budget``.
    Equality admits: the last full budget is enough for one mint. Unobserved
    procfs (``x_exhausted is None``) does not refuse, including when reserves
    are non-zero. Missing or malformed ``pins.toml`` reserves zero pins.
    """
    snap = probe_x_display(
        display=display,
        count=count,
        max_clients=max_clients,
        chrome_budget=chrome_budget,
        proc_net_unix=proc_net_unix,
    )
    if snap["x_exhausted"] is None:
        return snap
    reserved = reserved_chromes if reserved_chromes > 0 else 0
    reserved += pin_lanes_on_display(str(snap["x_display"]))
    headroom = snap["x_headroom"]
    budget = int(snap["x_chrome_client_budget"])
    short = isinstance(headroom, int) and headroom - reserved * budget < budget
    if snap["x_exhausted"] is True or short:
        with contextlib.suppress(Exception):
            from claude_bundles import cdp_registry_events as _events

            _events.emit(
                _events.cdp_display_exhausted(
                    display=str(snap["x_display"]),
                    x_clients=snap["x_clients"],
                    x_max_clients=int(snap["x_max_clients"]),
                    x_headroom=snap["x_headroom"],
                    x_chrome_client_budget=budget,
                    x_reserved_chromes=reserved,
                    after_drain=after_drain,
                )
            )
        raise XDisplayCapacityError(exhausted_message(snap))
    return snap


def x_display_wire_fields(snap: Mapping[str, Any]) -> dict[str, Any]:
    """Render X occupancy as qualified scalars for the active-work snapshot.

    Does not rewrite ``free_slots``; that formula stays stream admission.
    """
    fields: dict[str, Any] = {
        "x_display": snap.get("x_display"),
        "x_probe": snap.get("x_probe"),
    }
    fields.update(
        QualifiedScalar(
            value=snap.get("x_clients"),
            scope=_X_CLIENTS_SCOPE,
            authority=AuthorityClass.OBSERVED,
        ).emit("x_clients")
    )
    fields.update(
        QualifiedScalar(
            value=snap.get("x_max_clients"),
            scope=_X_MAX_SCOPE,
            authority=AuthorityClass.RECORDED,
        ).emit("x_max_clients")
    )
    fields.update(
        QualifiedScalar(
            value=snap.get("x_headroom"),
            scope=_X_HEADROOM_SCOPE,
            authority=AuthorityClass.OBSERVED,
        ).emit("x_headroom")
    )
    fields.update(
        QualifiedScalar(
            value=snap.get("x_exhausted"),
            scope=_X_EXHAUSTED_SCOPE,
            authority=AuthorityClass.OBSERVED,
        ).emit("x_exhausted")
    )
    fields.update(
        QualifiedScalar(
            value=snap.get("x_chrome_client_budget"),
            scope=_X_BUDGET_SCOPE,
            authority=AuthorityClass.RECORDED,
        ).emit("x_chrome_client_budget")
    )
    return fields


def attach_x_display_capacity(payload: dict[str, Any], decl: SurfaceDecl) -> None:
    """Mutate *payload* with X occupancy. Caller seals afterward.

    ``free_slots`` stays stream-admission. These keys make the second capacity
    model visible on the same snapshot so callers stop treating stream slots as
    window-mint room.
    """
    snap = probe_x_display()
    payload.update(x_display_wire_fields(snap))
    decl.plain("x_display", reason=str(snap["x_display"]))
    decl.plain("x_probe", reason="proc_net_unix | injected | unavailable")
