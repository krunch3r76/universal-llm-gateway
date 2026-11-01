"""Target-scoped read-only probe — never navigates or clicks."""

from __future__ import annotations

import asyncio
from typing import Any

from web_tab.cdp_port import CdpObservationPort, default_cdp_port
from web_tab.config import resolve_cdp_url
from web_tab.events import emit_probe_completed
from web_tab.observation import observe_actuation_plane
from web_tab.surface_adapter import probe_js_for_surface, unsupported_surface_error


async def probe_target(
    *,
    cdp_url: str | None = None,
    target_id: str | None = None,
    surface: str = "chatgpt",
    house_id: str | None = None,
    binding_conversation_id: str | None = None,
    port: CdpObservationPort | None = None,
) -> dict[str, Any]:
    """Read-only probe for one CDP target (or list-only when *target_id* omitted)."""
    try:
        resolved_cdp = resolve_cdp_url(cdp_url)
    except ValueError:
        return {"ok": False, "error": {"code": "missing_cdp_url"}}

    try:
        js = probe_js_for_surface(surface)
    except ValueError as exc:
        if str(exc).startswith("unsupported_surface:"):
            return unsupported_surface_error(surface)
        raise

    cdp = port or default_cdp_port()
    actuation = await observe_actuation_plane(
        cdp_url=resolved_cdp,
        target_id=target_id,
        port=cdp,
        probe_js=js,
        house_id=house_id,
        binding_conversation_id=binding_conversation_id,
        surface=surface,
    )
    result = {
        "ok": True,
        "read_only": True,
        "surface": surface,
        "house_id": house_id,
        "actuation": actuation,
        "_next": "web_tab_read(op=status) for both planes; bind is a later write slice.",
    }
    await asyncio.to_thread(
        emit_probe_completed,
        house_id=house_id or "",
        target_id=target_id or "",
        tab_count=len(actuation.get("tabs") or []),
    )
    return result
