"""Cursor display :8 adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from chat_harvest.models import ClassifyRefuse
from cursor_bridge.electron_chat_relay import ElectronEndpoint, read_endpoint_turn

from interlocutor.targets import DisplayEightTarget, DisplayGrokWindowTarget

_NON_RETRYABLE_TIMEOUT = frozenset(
    {
        "cdp_eval_timeout",
        "clicked_unknown",
        "delivery_unconfirmed",
        "send_unconfirmed",
        "unknown",
    }
)


def _endpoint(target: DisplayEightTarget) -> ElectronEndpoint:
    return ElectronEndpoint(
        label=f"{target.display}:{target.agent_id}",
        app=target.app,
        display=target.display,
        agent_id=target.agent_id,
        thread=target.agent_id,
    )


def _leaf(result: dict[str, Any]) -> dict[str, Any]:
    inner = result.get("result")
    if isinstance(inner, dict):
        return inner
    return result


def _verified_display_send(result: dict[str, Any]) -> bool:
    leaf = _leaf(result)
    if leaf.get("ok") is False:
        return False
    if leaf.get("delivered") is not True:
        return False
    if leaf.get("pasted") is False:
        return False
    return True


def _display_steer_failure(result: dict[str, Any]) -> dict[str, Any]:
    leaf = _leaf(result)
    reason = str(
        leaf.get("code")
        or result.get("code")
        or leaf.get("reason")
        or result.get("reason")
        or "delivery_failed"
    )
    code = str(result.get("code") or leaf.get("code") or reason or "unknown")
    if code in {"agent_id_mismatch", "target_unverified"}:
        code = "target_mismatch"
    out: dict[str, Any] = {
        "ok": False,
        "code": code,
        "reason": str(
            result.get("reason") or leaf.get("reason") or leaf.get("message") or code
        ),
        "adapter": "display_eight",
        "delivered": bool(leaf.get("delivered")),
        "pasted": leaf.get("pasted"),
    }
    if (
        leaf.get("clicked_unknown")
        or code in _NON_RETRYABLE_TIMEOUT
        or leaf.get("retryable") is False
    ):
        out["retryable"] = False
    if "clicked_unknown" in leaf:
        out["clicked_unknown"] = bool(leaf["clicked_unknown"])
    for key in (
        "send_clicked",
        "race_winner",
        "endpoint",
        "display",
        "app",
        "phase",
        "inserted",
    ):
        if key in leaf:
            out[key] = leaf[key]
    return out


def _display_steer_success(
    result: dict[str, Any], *, voice_s: str, target: DisplayEightTarget
) -> dict[str, Any]:
    leaf = _leaf(result)
    watermarked = voice_s == "operator_relay"
    # operator_paste.py joins the body on spaces and stamps a watermark nonce.
    fidelity = "flattened_watermark" if watermarked else "verbatim"
    out: dict[str, Any] = {
        "ok": True,
        "adapter": "display_eight",
        "voice": voice_s,
        "watermarked": watermarked,
        "fidelity": fidelity,
        "delivered": True,
        "pasted": leaf.get("pasted", True),
        "phase": leaf.get("phase") or "submitted_visible",
    }
    for key in (
        "endpoint",
        "display",
        "app",
        "selected",
        "message",
        "fingerprint",
        "active_agent_id",
        "active_title",
        "generating",
    ):
        if key in leaf:
            out[key] = leaf[key]
        elif key in result:
            out[key] = result[key]
    return out


async def read_display_latest(target: DisplayEightTarget) -> dict[str, Any]:
    snap = await read_endpoint_turn(_endpoint(target), select=True)
    if not snap.get("ok"):
        code = str(snap.get("code") or snap.get("reason") or "read_failed")
        return {
            "ok": False,
            "code": code,
            "reason": str(snap.get("reason") or snap.get("detail") or code),
        }
    recent = list(snap.get("recent_messages") or [])
    return {
        "ok": True,
        "adapter": "display_eight",
        "coverage": "latest",
        "latest_message": snap.get("latest_message") or "",
        "generating": bool(snap.get("generating")),
        "composer_draft": str(snap.get("composer_draft") or ""),
        "latest_fingerprint": str(snap.get("latest_fingerprint") or ""),
        "active_agent_id": snap.get("active_agent_id"),
        "active_title": snap.get("active_title"),
        "recent_messages": recent[-3:],
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
    body = text or ""
    if not body.strip():
        return ClassifyRefuse(
            code="text_required", reason="steer requires text"
        ).model_dump()

    voice_s = (voice or "self").strip().lower()
    if voice_s == "operator_relay":
        grant_s = (grant or "").strip().lower()
        auth = (authorization_source or "").strip()
        if grant_s not in {"operator", "explicit"} or not auth:
            return ClassifyRefuse(
                code="watermark_missing_authorization",
                reason="operator_relay requires grant (operator|explicit) and authorization_source",
            ).model_dump()
        from cursor_bridge.operator_paste import deliver_operator_paste

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

    if isinstance(result, dict) and result.get("ok") is False and result.get("code"):
        return {
            "ok": False,
            "code": str(result.get("code")),
            "reason": str(
                result.get("reason") or result.get("message") or result.get("code")
            ),
            **{
                k: v
                for k, v in result.items()
                if k not in {"code", "reason", "message"}
            },
        }

    if not _verified_display_send(result):
        failure = _display_steer_failure(result)
        leaf = _leaf(result)
        if str(leaf.get("reason") or "") in _NON_RETRYABLE_TIMEOUT:
            failure["retryable"] = False
        return failure

    return _display_steer_success(result, voice_s=voice_s, target=target)


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


def list_grok_window_sync(
    target: DisplayGrokWindowTarget,
    *,
    include_hidden: bool = True,
    include_retired: bool = False,
) -> dict[str, Any]:
    from cursor_bridge.grok_agent_ops import list_grok_agents

    out = list_grok_agents(
        target.display,
        include_hidden=include_hidden,
        include_retired=include_retired,
    )
    return {**out, "adapter": "display_grok_window", "op": "list"}


def spawn_grok_window_sync(
    target: DisplayGrokWindowTarget,
    *,
    name: str,
    description: str = "",
    hidden: bool = True,
    mailbox: str | None = None,
    thread_scope: str | None = None,
    text: str | None = None,
    voice: str = "self",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    from cursor_bridge.grok_agent_ops import spawn_grok_agent

    out = spawn_grok_agent(
        target.display,
        name,
        description=description,
        hidden=hidden,
        mailbox=mailbox,
        thread_scope=thread_scope,
        text=text,
        voice=voice,
        idempotency_key=idempotency_key,
    )
    return {**out, "adapter": "display_grok_window", "op": "spawn"}


def retire_grok_agent_sync(target: DisplayEightTarget) -> dict[str, Any]:
    from cursor_bridge.grok_agent_ops import retire_grok_agent

    out = retire_grok_agent(target.display, target.agent_id)
    return {**out, "adapter": "display_eight", "op": "retire"}


__all__ = [
    "list_grok_window_sync",
    "read_display_latest_sync",
    "retire_grok_agent_sync",
    "spawn_grok_window_sync",
    "steer_display_sync",
]
