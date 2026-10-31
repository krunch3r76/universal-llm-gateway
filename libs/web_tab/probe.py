"""Target-scoped read-only probe — never navigates or clicks."""

from __future__ import annotations

from typing import Any

from web_tab.cdp_port import CdpObservationPort, default_cdp_port
from web_tab.events import emit_probe_completed
from web_tab.observation import observe_actuation_plane
from web_tab.surface_adapter import probe_js_for_surface

DEFAULT_ORION_CDP_URL = "http://127.0.0.1:9342"


async def probe_target(
    *,
    cdp_url: str = DEFAULT_ORION_CDP_URL,
    target_id: str | None = None,
    surface: str = "chatgpt",
    house_id: str | None = None,
    port: CdpObservationPort | None = None,
) -> dict[str, Any]:
    """Read-only probe for one CDP target (or list-only when *target_id* omitted)."""
    cdp = port or default_cdp_port()
    js = probe_js_for_surface(surface)
    actuation = await observe_actuation_plane(
        cdp_url=cdp_url,
        target_id=target_id,
        port=cdp,
        probe_js=js,
    )
    result = {
        "ok": True,
        "read_only": True,
        "surface": surface,
        "house_id": house_id,
        "actuation": actuation,
        "_next": "web_tab_read(op=status) for both planes; bind is a later write slice.",
    }
    emit_probe_completed(
        house_id=house_id or "",
        target_id=target_id or "",
        tab_count=len(actuation.get("tabs") or []),
    )
    return result
