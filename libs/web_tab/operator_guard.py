"""Operator-active detection before agent paste (back off, retryable)."""

from __future__ import annotations

from typing import Any

from cursor_bridge.dom_composer_paste import (
    install_operator_activity_probe,
    read_operator_busy,
    wait_for_operator_idle,
)


async def operator_active_probe(
    ws_url: str,
    *,
    selector: str,
    watermark: str = "",
    tab_reused: bool = False,
    wait_for_idle: bool = True,
) -> dict[str, Any]:
    await install_operator_activity_probe(ws_url)
    if wait_for_idle:
        idle = await wait_for_operator_idle(
            ws_url, selector=selector, watermark=watermark, max_wait_s=30.0
        )
        if not idle.get("ok"):
            return {
                "ok": False,
                "code": "operator_active",
                "reason": str(idle.get("reason") or "operator_busy"),
                "retryable": True,
                "probe": idle,
            }
    probe = await read_operator_busy(ws_url, selector=selector, watermark=watermark)
    has_focus = probe.get("document_has_focus") is True
    visible = str(probe.get("document_visibility") or "") == "visible"
    operator_active = bool(probe.get("operator_active"))
    foreign = bool(probe.get("foreign_draft"))
    if tab_reused and (has_focus or visible):
        return {
            "ok": False,
            "code": "operator_active",
            "reason": "reused tab has focus or is visible to the operator",
            "retryable": True,
            "probe": probe,
        }
    if has_focus and operator_active:
        return {
            "ok": False,
            "code": "operator_active",
            "reason": "operator input or focus on target tab",
            "retryable": True,
            "probe": probe,
        }
    if operator_active or foreign:
        return {
            "ok": False,
            "code": "operator_active",
            "reason": "recent operator input or foreign draft in composer",
            "retryable": True,
            "probe": probe,
        }
    return {"ok": True, "probe": probe}
