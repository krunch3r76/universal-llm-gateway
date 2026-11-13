"""Interlocutor facade — read and steer orchestration."""

from __future__ import annotations

from typing import Any, Literal

from chat_harvest.models import ClassifyRefuse

from interlocutor import cowork_adapter, display_adapter, idempotency
from interlocutor.targets import (
    CoworkMaestroTarget,
    CoworkUrlTarget,
    DisplayEightTarget,
    refuse_dict,
    resolve_grok_window_target,
    resolve_target,
)

Coverage = Literal["latest", "from_turn", "full", "meta"]
Voice = Literal["self", "operator_relay"]


def _refuse(refuse: ClassifyRefuse) -> dict[str, Any]:
    return refuse_dict(refuse)


def execute_select(*, target: str) -> dict[str, Any]:
    resolved = resolve_target(target)
    if isinstance(resolved, ClassifyRefuse):
        return _refuse(resolved)
    if not isinstance(resolved, DisplayEightTarget):
        return _refuse(
            ClassifyRefuse(
                code="unsupported_target",
                reason="select requires display:<n>/<agent_id>[/grok]",
            )
        )
    if resolved.app != "grok":
        return _refuse(
            ClassifyRefuse(
                code="unsupported_app",
                reason="select is supported on grok displays only",
            )
        )
    return display_adapter.select_display_target_sync(resolved)


def execute_read(
    *,
    target: str,
    coverage: Coverage = "latest",
    after_turn: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    resolved = resolve_target(target)
    if isinstance(resolved, ClassifyRefuse):
        return _refuse(resolved)

    if isinstance(resolved, DisplayEightTarget):
        if coverage not in {"latest"}:
            return _refuse(
                ClassifyRefuse(
                    code="capability_unsupported",
                    reason=f"display :8 supports coverage=latest only (got {coverage!r})",
                )
            )
        return display_adapter.read_display_latest_sync(resolved)

    if isinstance(resolved, CoworkMaestroTarget):
        return cowork_adapter.harvest(
            after_turn=after_turn,
            limit=limit,
            parent_thread=resolved.parent_thread,
        )

    if isinstance(resolved, CoworkUrlTarget):
        return cowork_adapter.harvest(
            after_turn=after_turn,
            limit=limit,
            chat_url=resolved.url,
        )

    return _refuse(
        ClassifyRefuse(code="unsupported_target", reason="unresolved adapter")
    )


def execute_steer(
    *,
    target: str,
    text: str,
    voice: Voice = "self",
    grant: str | None = None,
    authorization_source: str | None = None,
    scope: str | None = None,
    idempotency_key: str | None = None,
    text_uri: str | None = None,
) -> dict[str, Any]:
    from interlocutor.text_body import resolve_interlocutor_text

    body_res = resolve_interlocutor_text(text=text, text_uri=text_uri)
    if body_res.get("ok") is not True:
        return {
            "ok": False,
            "code": str(body_res.get("code") or "text_required"),
            "reason": str(body_res.get("reason") or body_res.get("code") or "text_required"),
            "delivered": False,
            "pasted": False,
        }
    body = str(body_res.get("body") or "")

    resolved = resolve_target(target)
    if isinstance(resolved, ClassifyRefuse):
        return _refuse(resolved)

    voice_s = (voice or "self").strip().lower()
    if voice_s == "operator_relay":
        grant_s = (grant or "").strip().lower()
        auth = (authorization_source or "").strip()
        if grant_s not in {"operator", "explicit"} or not auth:
            return _refuse(
                ClassifyRefuse(
                    code="watermark_missing_authorization",
                    reason="operator_relay requires grant and authorization_source",
                )
            )

    decision = idempotency.reserve(
        target=target,
        idempotency_key=idempotency_key,
        text=body,
        voice=voice_s,
    )
    action = str(decision.get("action") or "")
    if action == "duplicate":
        return {**decision.get("receipt", {}), "duplicate": True}
    if action != "proceed":
        code = str(decision.get("code") or "idempotency_unknown")
        return {
            "ok": False,
            "code": code,
            "reason": code,
            "delivered": False,
            "pasted": False,
        }

    try:
        if isinstance(resolved, DisplayEightTarget):
            result = display_adapter.steer_display_sync(
                resolved,
                text=body,
                voice=voice_s,
                grant=grant,
                authorization_source=authorization_source,
                scope=scope,
            )
        elif isinstance(resolved, CoworkMaestroTarget):
            result = cowork_adapter.followup(
                prompt_text=body,
                parent_thread=resolved.parent_thread,
            )
        elif isinstance(resolved, CoworkUrlTarget):
            result = cowork_adapter.followup(prompt_text=body, chat_url=resolved.url)
        else:
            result = _refuse(
                ClassifyRefuse(code="unsupported_target", reason="unresolved adapter")
            )
    except Exception:
        idempotency.settle(
            target=target,
            idempotency_key=idempotency_key,
            text=body,
            voice=voice_s,
            receipt={"ok": False, "code": "unknown", "delivered": False},
        )
        raise
    idempotency.settle(
        target=target,
        idempotency_key=idempotency_key,
        text=body,
        voice=voice_s,
        receipt=result,
    )
    if body_res.get("large_delivered_via_pointer"):
        result = {**result, "large_delivered_via_pointer": True, "text_uri": text_uri}
    return result


def execute_list(
    *,
    target: str,
    include_hidden: bool = True,
    include_retired: bool = False,
) -> dict[str, Any]:
    resolved = resolve_grok_window_target(target)
    if isinstance(resolved, ClassifyRefuse):
        return _refuse(resolved)
    return display_adapter.list_grok_window_sync(
        resolved,
        include_hidden=include_hidden,
        include_retired=include_retired,
    )


def execute_spawn(
    *,
    target: str,
    name: str,
    description: str = "",
    hidden: bool = True,
    mailbox: str | None = None,
    thread_scope: str | None = None,
    text: str | None = None,
    voice: Voice = "self",
    grant: str | None = None,
    authorization_source: str | None = None,
    idempotency_key: str | None = None,
    text_uri: str | None = None,
) -> dict[str, Any]:
    """Spawn on a configured Grok window. Voice uses the existing steer contract."""
    from interlocutor.text_body import resolve_interlocutor_text

    resolved = resolve_grok_window_target(target)
    if isinstance(resolved, ClassifyRefuse):
        return _refuse(resolved)
    steer_text = (text or "").strip()
    if text_uri or steer_text:
        body_res = resolve_interlocutor_text(text=text, text_uri=text_uri)
        if body_res.get("ok") is not True:
            return {
                "ok": False,
                "code": str(body_res.get("code") or "text_required"),
                "reason": str(body_res.get("reason") or body_res.get("code")),
            }
        steer_text = str(body_res.get("body") or "")
    out = display_adapter.spawn_grok_window_sync(
        resolved,
        name=name,
        description=description,
        hidden=hidden,
        mailbox=mailbox,
        thread_scope=thread_scope,
        text=steer_text or None,
        voice=voice,
        grant=grant,
        authorization_source=authorization_source,
        idempotency_key=idempotency_key,
    )
    if text_uri and steer_text:
        out = {**out, "text_uri": text_uri}
    return out


def execute_retire(*, target: str) -> dict[str, Any]:
    resolved = resolve_target(target)
    if isinstance(resolved, ClassifyRefuse):
        return _refuse(resolved)
    if not isinstance(resolved, DisplayEightTarget):
        return _refuse(
            ClassifyRefuse(
                code="unsupported_target",
                reason="retire requires display:<n>/<agent_id>",
            )
        )
    from cursor_bridge.grok_window_bindings import cdp_port_for_display

    if cdp_port_for_display(resolved.display) is None:
        return _refuse(
            ClassifyRefuse(
                code="capability_unsupported",
                reason="retire is supported on registered grok window displays only",
            )
        )
    return display_adapter.retire_grok_agent_sync(resolved)


__all__ = [
    "execute_list",
    "execute_read",
    "execute_select",
    "execute_retire",
    "execute_spawn",
    "execute_steer",
]
