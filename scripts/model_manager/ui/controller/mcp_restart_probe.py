"""Busy probe for MCP restarts.

No active MCP work permits a restart. Active work is an MCP request still
inside ``/active-work`` ``in_flight``, other than the single manage call that
is asking for the restart (a:36950). A Cowork turn between POSTs
(``life_hot`` with ``in_flight`` 0) and a ``cdp_ask`` execution that is not
inside an MCP request are not MCP work. The container SIGTERM drain
(``middleware/drain.py``) still finishes whatever HTTP requests are open.

``cdp_ask`` is recorded on the detail for diagnosis and does not set
``busy``. MCP probe failure is best-effort when cdp_ask answered; when
cdp_ask is unconfigured, MCP failure fails closed.
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
    describe_probe_exc,
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


def _mcp_has_active_work(detail: dict[str, Any]) -> bool:
    """True when MCP still has a request that is not the lone restart call.

    ``in_flight`` 0 is idle, including when ``life_hot`` is still inside its
    TTL. ``in_flight`` 1 with ``life_hot`` false is the manage self-call
    (a:36950). ``in_flight`` 1 with ``life_hot`` true is one live life call.
    A payload with no ``in_flight`` fails closed on its ``busy`` bit.
    """
    if "in_flight" not in detail:
        life_hot = detail.get("life_hot")
        if isinstance(life_hot, bool):
            return life_hot
        return bool(detail.get("busy", True))
    try:
        in_flight = int(detail["in_flight"])
    except (TypeError, ValueError):
        return bool(detail.get("busy", True))
    if in_flight <= 0:
        return False
    if in_flight == 1 and detail.get("life_hot") is False:
        return False
    return True


class McpBusyProbe:
    """Busy when MCP has an in-flight request that is not the restart call."""

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

        if self._mcp is not None:
            try:
                mcp_work = await self._mcp.snapshot()
            except (httpx.HTTPError, ValueError, OSError) as exc:
                detail["mcp_probe_error"] = describe_probe_exc(exc)
                # Without cdp_ask we cannot soft-fail: an unreachable MCP that
                # still has a life client would look idle and get killed.
                if not cdp_seen:
                    raise
            else:
                detail["mcp"] = mcp_work.detail
                if _mcp_has_active_work(mcp_work.detail):
                    busy = True
                    detail["busy_reasons"].append("mcp_in_flight")

        detail["busy"] = busy
        summary_bits: list[str] = []
        if "mcp_in_flight" in detail["busy_reasons"]:
            mcp = detail.get("mcp") or {}
            summary_bits.append(f"mcp in_flight={mcp.get('in_flight')}")
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
        cdp_probe = HttpActiveWorkProbe(base, "/v1/project-ask/drain-state")
    mcp_probe = HttpActiveWorkProbe(mcp_active_work_base_url(), _MCP_ACTIVE_WORK_PATH)
    return McpBusyProbe(cdp_probe=cdp_probe, mcp_probe=mcp_probe)


__all__ = [
    "McpBusyProbe",
    "build_mcp_busy_probe",
    "mcp_active_work_base_url",
]
