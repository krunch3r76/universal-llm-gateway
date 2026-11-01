"""Connector vs actuation plane observation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from universal_protocol.status_basis import status_basis

from web_tab.cdp_port import CdpObservationPort
from web_tab.conversation_id import conversation_id_from_url
from web_tab.surface_adapter import url_in_surface_scope


def _coerce_signed_in(val: Any) -> bool | None:
    if isinstance(val, bool):
        return val
    if val is None:
        return None
    return None


def _tab_in_scope(
    tab: dict[str, str],
    *,
    house_id: str | None,
    target_id: str | None,
    binding_conversation_id: str | None,
) -> bool:
    tid = tab.get("id") or ""
    if target_id and tid == target_id:
        return True
    if binding_conversation_id:
        cid = conversation_id_from_url(tab.get("url") or "")
        if cid == binding_conversation_id:
            return True
    return False


async def observe_actuation_plane(
    *,
    cdp_url: str,
    target_id: str | None,
    port: CdpObservationPort,
    probe_js: str,
    house_id: str | None = None,
    binding_conversation_id: str | None = None,
    surface: str = "chatgpt",
) -> dict[str, Any]:
    """CDP actuation plane — list tabs and optional target-scoped evaluate."""
    now = datetime.now(tz=UTC).isoformat()
    listed = await asyncio.to_thread(port.list_tabs, cdp_url=cdp_url)
    pages: list[dict[str, str]] = listed.get("pages") or []
    reachable: bool | None = listed.get("reachable")
    list_error = listed.get("error")

    signed_in: bool | None = None
    stale = False
    scoped_url = ""
    conv_id: str | None = None
    cdp_eval_error: dict[str, Any] | None = None

    visible_tabs = [
        t
        for t in pages
        if _tab_in_scope(
            t,
            house_id=house_id,
            target_id=target_id,
            binding_conversation_id=binding_conversation_id,
        )
        and url_in_surface_scope(surface, t.get("url") or "")
    ]
    other_count = max(0, len(pages) - len(visible_tabs))

    out_of_scope = False
    if target_id:
        match = next((t for t in pages if t.get("id") == target_id), None)
        if match is None:
            stale = True
        else:
            scoped_url = match.get("url") or ""
            conv_id = conversation_id_from_url(scoped_url)
            if not scoped_url:
                stale = True
            elif not url_in_surface_scope(surface, scoped_url):
                out_of_scope = True
            else:
                raw = await port.evaluate_on_target(
                    cdp_url=cdp_url,
                    target_id=target_id,
                    expression=probe_js,
                    tab_row=match,
                )
                err = raw.get("error")
                err_code = err.get("code") if isinstance(err, dict) else None
                if err_code == "target_out_of_scope":
                    out_of_scope = True
                elif err_code in ("cdp_attach_failed", "cdp_timeout"):
                    cdp_eval_error = err if isinstance(err, dict) else None
                    signed_in = None
                elif raw.get("stale"):
                    stale = True
                else:
                    signed_in = _coerce_signed_in(raw.get("signed_in"))

    cdp_reachable_val: bool | None
    if reachable is False:
        cdp_reachable_val = False
    elif reachable is True:
        cdp_reachable_val = True
    else:
        cdp_reachable_val = None

    return {
        "plane": "actuation",
        "cdp_url": cdp_url,
        "target_id": target_id,
        "cdp_reachable": status_basis(
            "cdp_reachable",
            cdp_reachable_val,
            as_of=now,
            source="cdp.json_list",
            scope=f"cdp:{cdp_url}",
            epoch={
                "tab_count": len(pages),
                "list_error": list_error,
                "other_tab_count": other_count,
            },
        ),
        "signed_in": status_basis(
            "signed_in",
            signed_in,
            as_of=now,
            source=(
                "cdp.refused"
                if out_of_scope
                else (
                    "cdp.error"
                    if cdp_eval_error is not None
                    else ("cdp.evaluate" if target_id else "cdp.unscoped")
                )
            ),
            scope=f"target:{target_id or 'none'}",
            epoch={"url": scoped_url, "conversation_id": conv_id},
            stale=stale,
        ),
        **({"error": {"code": "target_out_of_scope"}} if out_of_scope else {}),
        **({"error": cdp_eval_error} if cdp_eval_error else {}),
        "tabs": [
            {
                "target_id": t.get("id"),
                "url": t.get("url"),
                "conversation_id": conversation_id_from_url(t.get("url") or ""),
                "title": t.get("title"),
            }
            for t in visible_tabs
        ],
        "other_tab_count": other_count,
    }
