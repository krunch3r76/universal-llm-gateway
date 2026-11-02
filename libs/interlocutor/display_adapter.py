"""Cursor display :8 adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from chat_harvest.models import ClassifyRefuse
from cursor_bridge.electron_chat_relay import ElectronEndpoint, read_endpoint_turn
from cursor_bridge.operator_paste import deliver_operator_paste
from interlocutor.targets import DisplayEightTarget


def _endpoint(target: DisplayEightTarget) -> ElectronEndpoint:
    return ElectronEndpoint(
        label=f"{target.display}:{target.agent_id}",
        app=target.app,
        display=target.display,
        agent_id=target.agent_id,
        thread=target.agent_id,
    )


async def read_display_latest(target: DisplayEightTarget) -> dict[str, Any]:
    snap = await read_endpoint_turn(_endpoint(target), select=True)
    if not snap.get("ok"):
        code = str(snap.get("code") or snap.get("reason") or "read_failed")
        return {
            "ok": False,
            "code": code,
            "reason": str(snap.get("reason") or snap.get("detail") or code),
        }
    return {
        "ok": True,
        "adapter": "display_eight",
        "coverage": "latest",
        "latest_message": snap.get("latest_message") or "",
        "generating": bool(snap.get("generating")),
        "endpoint": snap.get("endpoint"),
    }


async def steer_display(
    target: DisplayEightTarget,
    *,
    text: str,
    voice: str,
    grant: str | None,
    authorization_source: str | None,
    scope: str | None,
) -> dict[str, Any]:
    body = (text or "").strip()
    if not body:
        return ClassifyRefuse(code="text_required", reason="steer requires text").model_dump()

    voice_s = (voice or "self").strip().lower()
    if voice_s == "operator_relay":
        grant_s = (grant or "").strip().lower()
        auth = (authorization_source or "").strip()
        if grant_s not in {"operator", "explicit"} or not auth:
            return ClassifyRefuse(
                code="watermark_missing_authorization",
                reason="operator_relay requires grant (operator|explicit) and authorization_source",
            ).model_dump()
        result = await deliver_operator_paste(
            display=target.display,
            app=target.app,
            agent_id=target.agent_id,
            text=body,
            grant=grant_s,
            authorization_source=auth,
            scope=str(scope or ""),
            select=True,
        )
    elif voice_s == "self":
        from cursor_bridge.electron_chat_relay import paste_endpoint_message

        result = await paste_endpoint_message(_endpoint(target), body, select=True)
    else:
        return ClassifyRefuse(
            code="unsupported_voice",
            reason=f"unsupported voice: {voice!r}",
        ).model_dump()

    if not result.get("ok", True) and result.get("code"):
        return {
            "ok": False,
            "code": str(result.get("code")),
            "reason": str(result.get("reason") or result.get("message") or result.get("code")),
            **{k: v for k, v in result.items() if k not in {"code", "reason", "message"}},
        }
    watermarked = voice_s == "operator_relay"
    return {
        "ok": True,
        "adapter": "display_eight",
        "voice": voice_s,
        "watermarked": watermarked,
        "fidelity": "flattened" if target.app in {"grok", "cursor"} else "verbatim",
        "result": result,
    }


def read_display_latest_sync(target: DisplayEightTarget) -> dict[str, Any]:
    return asyncio.run(read_display_latest(target))


def steer_display_sync(
    target: DisplayEightTarget,
    *,
    text: str,
    voice: str,
    grant: str | None,
    authorization_source: str | None,
    scope: str | None,
) -> dict[str, Any]:
    return asyncio.run(
        steer_display(
            target,
            text=text,
            voice=voice,
            grant=grant,
            authorization_source=authorization_source,
            scope=scope,
        )
    )


__all__ = ["read_display_latest_sync", "steer_display_sync"]
