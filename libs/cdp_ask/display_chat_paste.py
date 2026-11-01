"""Execute grant-gated display-chat operator paste."""

from __future__ import annotations

from cursor_bridge.operator_paste import deliver_operator_paste

from cdp_ask.display_chat_events import display_chat_pasted, emit
from cdp_ask.display_chat_models import (
    DisplayChatPasteRequest,
    DisplayChatPasteResponse,
)


def _to_response(
    raw: dict[str, object], *, req: DisplayChatPasteRequest
) -> DisplayChatPasteResponse:
    ok = bool(raw.get("ok"))
    reason = str(raw.get("reason") or "")
    code = None if ok else (reason or "refused")
    return DisplayChatPasteResponse(
        ok=ok,
        code=code,
        reason=reason or None,
        display=str(raw.get("display") or req.display),
        app=str(raw.get("app") or req.app),
        agent_id=str(raw.get("agent_id") or req.agent_id),
        delivered=bool(raw.get("delivered")),
        readback_ok=bool(raw.get("readback_ok")),
        sha256_dom=str(raw.get("sha256_dom") or "") or None,
        sha256_png=str(raw.get("sha256_png") or "") or None,
        composer_empty_after=raw.get("composer_empty_after"),  # type: ignore[arg-type]
        assistant_began=raw.get("assistant_began"),  # type: ignore[arg-type]
        watermark_visible=raw.get("watermark_visible"),  # type: ignore[arg-type]
    )


async def execute_display_chat_paste(
    req: DisplayChatPasteRequest,
) -> DisplayChatPasteResponse:
    raw = await deliver_operator_paste(
        display=req.display,
        app=req.app,
        agent_id=req.agent_id,
        text=req.text,
        grant=req.grant,
        authorization_source=req.authorization_source,
        scope=req.scope,
        select=req.select,
    )
    response = _to_response(raw, req=req)
    emit(
        display_chat_pasted(
            display=req.display,
            app=req.app,
            agent_id=req.agent_id,
            ok=response.ok,
            grant=req.grant,
            authorization_source=req.authorization_source,
            sha256_dom=response.sha256_dom,
            sha256_png=response.sha256_png,
            code=response.code,
        )
    )
    return response
