"""Observed post-state for manage restart/rebuild — unknown ≠ live.

Authoring bar (6584 ii): return what was observed about the service after the
action, not an assertion that the lifecycle message succeeded. Callers must not
read top-level ``status`` as liveness unless ``service_live`` is explicitly True.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ...model.service_state import ServiceState, ServiceStatus

SERVICE_POST_STATE_SCOPE = "ServiceState.check_<service> immediately after lifecycle"


@dataclass(frozen=True, slots=True)
class ServicePostStateProbe:
    """Health probe after a restart/rebuild lifecycle."""

    probe_status: Literal["ok", "error"]
    service: str
    service_status: str | None = None
    detail: str = ""
    pid: int | None = None
    evaluated_scope: str = SERVICE_POST_STATE_SCOPE
    error_class: str | None = None
    restart_scheduled: bool = False

    @property
    def observable(self) -> bool:
        return self.probe_status == "ok"

    @property
    def service_live(self) -> bool | None:
        """True when observed running; False when observed not running; None if unobservable."""
        if self.restart_scheduled:
            return None
        if not self.observable:
            return None
        return self.service_status == ServiceStatus.RUNNING.value

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "probe_status": self.probe_status,
            "service": self.service,
            "evaluated_scope": self.evaluated_scope,
        }
        if self.restart_scheduled:
            out["restart_scheduled"] = True
            out["post_action_liveness"] = "unobservable"
        elif self.observable:
            out["service_status"] = self.service_status
            if self.detail:
                out["detail"] = self.detail
            if self.pid is not None:
                out["pid"] = self.pid
            out["post_action_liveness"] = (
                "live" if self.service_live else "not_live"
            )
        else:
            out["post_action_liveness"] = "unobservable"
            if self.error_class is not None:
                out["probe_error_class"] = self.error_class
        return out


def probe_service_post_state(
    svc: ServiceState,
    service: str,
    *,
    check_one: Any,
) -> ServicePostStateProbe:
    """Run one synchronous health check; map failures to unobservable (not empty)."""
    try:
        info = check_one(svc, service)
    except Exception as exc:
        return ServicePostStateProbe(
            probe_status="error",
            service=service,
            error_class=type(exc).__name__,
        )
    return ServicePostStateProbe(
        probe_status="ok",
        service=service,
        service_status=info.status.value,
        detail=str(info.detail or ""),
        pid=info.pid,
    )


def finalize_restart_rebuild_result(
    result: dict[str, Any],
    probe: ServicePostStateProbe,
) -> dict[str, Any]:
    """Attach post-state and derive top-level status/liveness from observation."""
    if result.get("status") == "deferred":
        return result

    out = dict(result)
    out["service_post_state"] = probe.to_dict()
    out["service_live"] = probe.service_live

    if probe.restart_scheduled:
        out["status"] = "scheduled"
        return out

    if not probe.observable:
        out["status"] = "completed"
        return out

    if probe.service_live:
        out["status"] = "ok"
    else:
        out["status"] = "completed"
    return out
