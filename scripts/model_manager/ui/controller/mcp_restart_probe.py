"""Busy probes that keep MCP restarts from cutting live Cowork / life sessions.

MCP's container drain only counts open HTTP requests. A Cowork turn spends most
of its wall time between POSTs (model thinking, tool planning), so
``in_flight=0`` is the normal state of a live life session — and a manage
``NullBusyProbe`` treated that as permission to stop the container.

This module gates MCP (and reports honest busy for ``busy_status``) on:
1. ``cdp_ask`` active-work — recorded executions **or** observed live CSE
2. MCP ``/active-work`` — in-flight HTTP plus a short life-tools activity TTL

Either signal alone is enough to defer. MCP probe failure is best-effort when
cdp_ask answered; when cdp_ask is unconfigured, MCP failure fails closed.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from deploy_identity.mcp_health_probe_url import resolve_mcp_health_probe_url

from .restart_drain import (
    RETRY_AFTER_S,
    ActiveWork,
    BusyProbe,
    HttpActiveWorkProbe,
)
from .service_config import cdp_ask_url_config

_MCP_ACTIVE_WORK_PATH = "/active-work"


def _origin_from_health_url(health_url: str) -> str:
    """Map ``…/health`` (or any URL) to the service origin used for probes."""
    parsed = urlparse(health_url.strip())
    path = parsed.path.rstrip("/")
    if path.endswith("/health"):
        path = path[: -len("/health")]
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def mcp_active_work_base_url() -> str:
    """Base URL for MCP ``/active-work`` (same origin resolution as /health)."""
    return _origin_from_health_url(resolve_mcp_health_probe_url())


def _cdp_busy(detail: dict[str, Any]) -> bool:
    """True when cdp_ask reports recorded or observed in-flight Cowork work."""
    if bool(detail.get("busy")):
        return True
    try:
        if int(detail.get("live_cse_count") or 0) > 0:
            return True
        if int(detail.get("running_count") or 0) > 0:
            return True
        if int(detail.get("effective_count") or 0) > 0:
            return True
    except (TypeError, ValueError):
        return bool(detail.get("busy"))
    return False


class McpBusyProbe:
    """Composite busy probe for ``service=mcp`` restart drain."""

    def __init__(
        self,
        *,
        cdp_probe: BusyProbe | None,
        mcp_probe: BusyProbe | None,
    ) -> None:
        self._cdp = cdp_probe
        self._mcp = mcp_probe

    async def snapshot(self) -> ActiveWork:
        detail: dict[str, Any] = {
            "busy_reasons": [],
            "retry_after_s": RETRY_AFTER_S,
        }
        busy = False
        cdp_seen = False

        if self._cdp is not None:
            cdp_work = await self._cdp.snapshot()
            cdp_seen = True
            detail["cdp_ask"] = cdp_work.detail
            if _cdp_busy(cdp_work.detail):
                busy = True
                detail["busy_reasons"].append("cdp_ask_live")

        if self._mcp is not None:
            try:
                mcp_work = await self._mcp.snapshot()
            except (httpx.HTTPError, ValueError, OSError) as exc:
                detail["mcp_probe_error"] = str(exc)
                # Without cdp_ask we cannot soft-fail: an unreachable MCP that
                # still has a life client would look idle and get killed.
                if not cdp_seen:
                    raise
            else:
                detail["mcp"] = mcp_work.detail
                if mcp_work.busy:
                    busy = True
                    detail["busy_reasons"].append("mcp_session_hot")

        detail["busy"] = busy
        summary_bits: list[str] = []
        if "cdp_ask_live" in detail["busy_reasons"]:
            cdp = detail.get("cdp_ask") or {}
            summary_bits.append(
                f"cdp_ask live_cse={cdp.get('live_cse_count')} "
                f"running={cdp.get('running_count')}"
            )
        if "mcp_session_hot" in detail["busy_reasons"]:
            mcp = detail.get("mcp") or {}
            summary_bits.append(
                f"mcp in_flight={mcp.get('in_flight')} "
                f"life_idle_s={mcp.get('life_idle_s')}"
            )
        if summary_bits:
            detail["active_count"] = 1
            detail["active_ops"] = [
                {
                    "kind": "mcp_life_session",
                    "subject_preview": "; ".join(summary_bits),
                }
            ]
        return ActiveWork(busy=busy, detail=detail)


def build_mcp_busy_probe() -> McpBusyProbe:
    """Construct the default MCP restart probe from live URL config."""
    cdp_probe: BusyProbe | None = None
    cfg = cdp_ask_url_config()
    if cfg is not None:
        _host, _port, base = cfg
        cdp_probe = HttpActiveWorkProbe(base, "/v1/project-ask/active-work")
    mcp_probe = HttpActiveWorkProbe(mcp_active_work_base_url(), _MCP_ACTIVE_WORK_PATH)
    return McpBusyProbe(cdp_probe=cdp_probe, mcp_probe=mcp_probe)


__all__ = [
    "McpBusyProbe",
    "build_mcp_busy_probe",
    "mcp_active_work_base_url",
]
