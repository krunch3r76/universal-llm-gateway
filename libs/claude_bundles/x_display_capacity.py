"""X11/Xvfb client occupancy for CDP Chrome mint — a distinct capacity axis.

``free_slots`` on active-work counts recorded project-ask *streams* (soft=2,
hard=3). Xvfb ``-maxclients`` counts unix connections on the display. A
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
from collections.abc import Mapping
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
_X_MAX_SCOPE = "configured X MaxClients (CDP_X_MAX_CLIENTS, default 64)"
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
    raw = str(display or "").strip()
    if raw.startswith(":"):
        raw = raw[1:]
    number = raw.split(".", 1)[0] or "2"
    return f"X{number}"


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


def _max_clients() -> int:
    raw = os.environ.get("CDP_X_MAX_CLIENTS", "").strip()
    if raw:
        with contextlib.suppress(ValueError):
            value = int(raw)
            if value > 0:
                return value
    return X_MAX_CLIENTS_DEFAULT


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
    cap = _max_clients() if max_clients is None else max_clients
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


def log_bytes_show_x_exhaustion(log_path: str, *, start_offset: int = 0) -> bool:
    """True when *this launch's* appended log bytes contain the MaxClients token.

    The Chrome log is opened append-only, so a prior failed mint would otherwise
    poison every later timeout. Only bytes after ``start_offset`` count.
    """
    path = Path(log_path)
    try:
        data = path.read_bytes()
    except OSError:
        return False
    chunk = data[max(0, start_offset) :]
    return X_MAX_CLIENTS_TOKEN.encode("utf-8") in chunk


def listen_timeout_x_message(port: int, log_path: str) -> str:
    """Replace the 20s Chrome-timeout string when the log named X."""
    return (
        f"Chrome on :{port} did not reach CDP because X display reported "
        f"{X_MAX_CLIENTS_TOKEN!r} in {log_path} "
        f"(not a browser hang)"
    )


def require_chrome_headroom(
    *,
    display: str | None = None,
    count: int | None = None,
    max_clients: int | None = None,
    chrome_budget: int | None = None,
    proc_net_unix: Path | None = None,
) -> dict[str, Any]:
    """Refuse mint when observed X headroom cannot host one multiprocess Chrome.

    Unobserved (procfs unreadable) does not refuse — the listen-timeout log
    scrape is the fallback for that miss.
    """
    snap = probe_x_display(
        display=display,
        count=count,
        max_clients=max_clients,
        chrome_budget=chrome_budget,
        proc_net_unix=proc_net_unix,
    )
    if snap["x_exhausted"] is True:
        with contextlib.suppress(Exception):
            from claude_bundles import cdp_registry_events as _events

            _events.emit(
                _events.cdp_display_exhausted(
                    display=str(snap["x_display"]),
                    x_clients=snap["x_clients"],
                    x_max_clients=int(snap["x_max_clients"]),
                    x_headroom=snap["x_headroom"],
                    x_chrome_client_budget=int(snap["x_chrome_client_budget"]),
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
    payload.update(x_display_wire_fields(probe_x_display()))
    decl.plain("x_display", reason="CDP_DISPLAY / DISPLAY / :2 for this mint host")
    decl.plain("x_probe", reason="proc_net_unix | injected | unavailable")
