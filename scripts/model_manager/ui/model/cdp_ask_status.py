"""Hub status for the remote cdp-ask satellite.

``ServiceState.check_cdp_ask`` delegates here so the manage Services line can
tell a refused Jupiter port from a slow ``GET /health``. Remote probes never
read or write the hub pidfile: that file lives on the satellite, and a copied
pid would be tested against the hub ``/proc`` and look dead.
"""

from __future__ import annotations

import http.client
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..controller.service_config import (
    cdp_ask_manage_state,
    cdp_ask_url_config,
    is_cdp_ask_local_host,
)
from .service_state import ServiceInfo, ServiceOwnership, ServiceStatus

if TYPE_CHECKING:
    from .service_state import ServiceState

HEALTH_TIMEOUT_S = 2.0


@dataclass(slots=True, kw_only=True)
class CdpAskHealthObservation:
    """Structured ``GET /health`` result for the cdp-ask satellite.

    Callers classify liveness from ``ok`` and ``fail_class`` instead of
    collapsing every miss into stopped. ``pid`` is the process id from the
    JSON body when the satellite reported one. ``detail`` is a short probe
    note (hygiene on success, fail class on error).
    """

    ok: bool
    pid: int | None
    fail_class: str | None
    detail: str | None


def observe_cdp_ask_health(
    host: str,
    port: int,
    *,
    timeout: float = HEALTH_TIMEOUT_S,
) -> CdpAskHealthObservation:
    """Probe ``GET /health`` and return ok, remote pid, fail class, and detail.

    ``fail_class`` is ``None`` on success. Refused, timeout, non-200, a
    non-ok status, and other transport errors stay distinct so a slow
    satellite is not reported as stopped. Performs one HTTP GET; no pidfile
    or process mutation.
    """
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request("GET", "/health")
            resp = conn.getresponse()
            if resp.status != 200:
                return CdpAskHealthObservation(
                    ok=False,
                    pid=None,
                    fail_class="http",
                    detail=f"/health returned {resp.status}",
                )
            raw = resp.read().decode("utf-8", errors="replace")
            body = json.loads(raw)
            if not isinstance(body, dict):
                return _transport("json")
            if body.get("status") != "ok":
                return CdpAskHealthObservation(
                    ok=False,
                    pid=None,
                    fail_class="status",
                    detail=f"status={body.get('status')}",
                )
            hygiene = body.get("registry_hygiene")
            detail = f"registry_hygiene={hygiene}" if hygiene else None
            return CdpAskHealthObservation(
                ok=True,
                pid=_pid_from_body(body.get("pid")),
                fail_class=None,
                detail=detail,
            )
        finally:
            conn.close()
    except ConnectionRefusedError:
        return CdpAskHealthObservation(
            ok=False,
            pid=None,
            fail_class="refused",
            detail="health probe failed: ConnectionRefusedError",
        )
    except TimeoutError:
        return CdpAskHealthObservation(
            ok=False,
            pid=None,
            fail_class="timeout",
            detail="health probe failed: TimeoutError",
        )
    except Exception as exc:
        return _transport(type(exc).__name__)


def check_cdp_ask(state: ServiceState) -> ServiceInfo:
    """Return cdp-ask status for the manage Services line.

    Unset URL is not enabled. A remote host (not this machine) uses the
    TCP accept plus ``/health`` pid, and never the hub pidfile. Localhost
    keeps pidfile and listener refresh. Timeout or a non-ok body while the
    port accepts is unhealthy, not stopped.
    """
    manage_state = cdp_ask_manage_state()
    if manage_state == "not_enabled":
        return ServiceInfo(
            name="cdp-ask",
            status=ServiceStatus.NOT_ENABLED,
            detail="PROJECT_ASK_URL unset",
        )

    url_cfg = cdp_ask_url_config()
    if url_cfg is None:
        return ServiceInfo(
            name="cdp-ask",
            status=ServiceStatus.NOT_ENABLED,
            detail="PROJECT_ASK_URL unset",
        )
    host, port, base_url = url_cfg
    health_url = f"{base_url}/health"
    probe_host = "127.0.0.1" if host in {"localhost", "127.0.0.1", "::1"} else host

    if manage_state == "disabled":
        return _disabled(probe_host, port, health_url)

    if is_cdp_ask_local_host(host):
        return _check_local(state, probe_host, port, health_url)
    return _check_remote(state, probe_host, port, health_url)


def _transport(exc_name: str) -> CdpAskHealthObservation:
    return CdpAskHealthObservation(
        ok=False,
        pid=None,
        fail_class="transport",
        detail=f"health probe failed: {exc_name}",
    )


def _pid_from_body(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _disabled(probe_host: str, port: int, health_url: str) -> ServiceInfo:
    obs = observe_cdp_ask_health(probe_host, port)
    observed = "process running" if obs.ok else "process stopped/unhealthy"
    detail = f"disabled; {observed}"
    if obs.detail:
        detail = f"{detail} ({obs.detail})"
    return ServiceInfo(
        name="cdp-ask",
        status=ServiceStatus.DISABLED,
        port=port,
        health_url=health_url,
        detail=detail,
        ownership=ServiceOwnership.UNKNOWN,
    )


def _check_remote(
    state: ServiceState, probe_host: str, port: int, health_url: str
) -> ServiceInfo:
    """Classify a satellite this host does not run.

    Hub ``ss`` and the hub pidfile cannot see Jupiter. Identity comes from
    the health JSON pid. A closed or refused port is stopped; any other
    miss while the port accepts is unhealthy.
    """
    if not state._port_open(port, probe_host):
        return _stopped(port, health_url)
    obs = observe_cdp_ask_health(probe_host, port)
    if obs.ok:
        return ServiceInfo(
            name="cdp-ask",
            status=ServiceStatus.RUNNING,
            port=port,
            pid=obs.pid,
            health_url=health_url,
            detail=_remote_running_detail(obs),
            ownership=ServiceOwnership.MANAGED,
        )
    if obs.fail_class == "refused":
        return _stopped(port, health_url)
    return ServiceInfo(
        name="cdp-ask",
        status=ServiceStatus.UNHEALTHY,
        port=port,
        health_url=health_url,
        detail=obs.detail or "health probe failed",
        ownership=ServiceOwnership.MANAGED,
    )


def _remote_running_detail(obs: CdpAskHealthObservation) -> str:
    parts: list[str] = []
    if obs.pid is not None:
        parts.append(f"PID {obs.pid}")
    if obs.detail:
        parts.append(obs.detail)
    return "; ".join(parts)


def _stopped(port: int, health_url: str) -> ServiceInfo:
    return ServiceInfo(
        name="cdp-ask",
        status=ServiceStatus.STOPPED,
        port=port,
        health_url=health_url,
        detail="",
        ownership=ServiceOwnership.MANAGED,
    )


def _check_local(
    state: ServiceState, probe_host: str, port: int, health_url: str
) -> ServiceInfo:
    """Localhost keeps pidfile identity and listener refresh."""
    pid, pid_note = state._resolve_pid_file(state.CDP_ASK_PID_FILE)
    port_open = state._port_open(port, probe_host)
    listener_pid = state._find_listener_pid(port) if port_open else None
    if listener_pid is not None and listener_pid != pid:
        state._write_pid_file(state.CDP_ASK_PID_FILE, listener_pid)
        pid = listener_pid
        pid_note = state._merge_notes(pid_note, "PID file refreshed from live listener")
    if port_open:
        obs = observe_cdp_ask_health(probe_host, port)
        healthy, health_detail = obs.ok, obs.detail
    else:
        healthy, health_detail = False, None
    if pid is not None:
        uptime = state._proc_uptime_str(pid)
        uptime_str = f" ({uptime})" if uptime else ""
        detail = state._with_note(
            f"PID {pid}{uptime_str}"
            + ("" if healthy else f", {health_detail or 'health probe failed'}"),
            pid_note,
        )
        if healthy and health_detail:
            detail = state._with_note(detail, health_detail)
        return ServiceInfo(
            name="cdp-ask",
            status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
            port=port,
            pid=pid,
            health_url=health_url,
            detail=detail,
            ownership=ServiceOwnership.MANAGED,
        )
    if healthy:
        detail = state._with_note(
            state._merge_notes("Port open (PID file missing)", health_detail) or "",
            pid_note,
        )
        return ServiceInfo(
            name="cdp-ask",
            status=ServiceStatus.RUNNING,
            port=port,
            health_url=health_url,
            detail=detail or "Port open (PID file missing)",
            ownership=ServiceOwnership.MANAGED,
        )
    return ServiceInfo(
        name="cdp-ask",
        status=ServiceStatus.STOPPED,
        port=port,
        health_url=health_url,
        detail=pid_note or "",
        ownership=ServiceOwnership.MANAGED,
    )
