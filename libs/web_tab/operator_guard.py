"""Operator-active detection before agent paste (back off, retryable)."""

from __future__ import annotations

from typing import Any

from cursor_bridge.dom_composer_paste import (
    install_operator_activity_probe,
    probe_foreign_draft,
    read_operator_busy,
    wait_for_operator_idle,
)

_BEFORE_LOCK_MAX_WAIT_S = 30.0
_UNDER_LOCK_MAX_WAIT_S = 2.0


def _operator_busy_result(*, reason: str, probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "operator_active",
        "reason": reason,
        "retryable": True,
        "probe": probe,
    }


async def wait_operator_idle_before_lock(
    ws_url: str,
    *,
    selector: str,
    watermark: str = "",
    expect_draft_sha256: str | None = None,
    max_wait_s: float = _BEFORE_LOCK_MAX_WAIT_S,
) -> dict[str, Any]:
    """Long idle wait — call before ``target_write_lock`` (lock must not be held)."""
    await install_operator_activity_probe(ws_url)
    idle = await wait_for_operator_idle(
        ws_url,
        selector=selector,
        watermark=watermark,
        max_wait_s=max_wait_s,
        expect_draft_sha256=expect_draft_sha256,
    )
    if not idle.get("ok"):
        return _operator_busy_result(
            reason=str(idle.get("reason") or "operator_busy"),
            probe=idle if isinstance(idle, dict) else {},
        )
    return {"ok": True}


async def operator_recheck_under_lock(
    ws_url: str,
    *,
    selector: str,
    watermark: str = "",
    created_by_agent: bool = True,
    lock_holder: bool = False,
    expect_draft_sha256: str | None = None,
    max_wait_s: float = _UNDER_LOCK_MAX_WAIT_S,
) -> dict[str, Any]:
    """Short re-check after acquiring the target lock."""
    if max_wait_s > 0:
        idle = await wait_for_operator_idle(
            ws_url,
            selector=selector,
            watermark=watermark,
            max_wait_s=max_wait_s,
            expect_draft_sha256=expect_draft_sha256,
        )
        if not idle.get("ok"):
            return _operator_busy_result(
                reason=str(idle.get("reason") or "operator_busy"),
                probe=idle if isinstance(idle, dict) else {},
            )
    probe = await read_operator_busy(ws_url, selector=selector, watermark=watermark)
    has_focus = probe.get("document_has_focus") is True
    visible = str(probe.get("document_visibility") or "") == "visible"
    operator_active = bool(probe.get("operator_active"))
    foreign = probe_foreign_draft(
        probe, expect_draft_sha256=expect_draft_sha256
    )
    if not lock_holder and not created_by_agent and (has_focus or visible):
        return _operator_busy_result(
            reason="tab not created by agent has focus or is visible to the operator",
            probe=probe,
        )
    if operator_active or foreign:
        return _operator_busy_result(
            reason="recent operator input or foreign draft in composer",
            probe=probe,
        )
    return {"ok": True, "probe": probe}


async def operator_active_probe(
    ws_url: str,
    *,
    selector: str,
    watermark: str = "",
    created_by_agent: bool = True,
    wait_for_idle: bool = True,
    lock_holder: bool = False,
    expect_draft_sha256: str | None = None,
) -> dict[str, Any]:
    """Legacy single-call probe (long wait + read). Prefer split before/under lock in write_ops."""
    if wait_for_idle:
        pre = await wait_operator_idle_before_lock(
            ws_url,
            selector=selector,
            watermark=watermark,
            expect_draft_sha256=expect_draft_sha256,
        )
        if not pre.get("ok"):
            return pre
    return await operator_recheck_under_lock(
        ws_url,
        selector=selector,
        watermark=watermark,
        created_by_agent=created_by_agent,
        lock_holder=lock_holder,
        expect_draft_sha256=expect_draft_sha256,
        max_wait_s=_UNDER_LOCK_MAX_WAIT_S if wait_for_idle else 0.0,
    )
