"""Operator-active detection before agent paste (back off, retryable)."""

from __future__ import annotations

from typing import Any

from cursor_bridge.dom_composer_paste import (
    install_operator_activity_probe,
    read_operator_busy,
)


async def operator_active_probe(
    ws_url: str,
    *,
    selector: str,
    watermark: str = "",
) -> dict[str, Any]:
    await install_operator_activity_probe(ws_url)
    probe = await read_operator_busy(ws_url, selector=selector, watermark=watermark)
    visible = probe.get("document_has_focus")
    operator_active = bool(probe.get("operator_active"))
    foreign = bool(probe.get("foreign_draft"))
    if visible is True and operator_active:
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
