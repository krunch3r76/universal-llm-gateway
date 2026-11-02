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
    resolve_target,
)

Coverage = Literal["latest", "from_turn", "full", "meta"]
Voice = Literal["self", "operator_relay"]


def _refuse(refuse: ClassifyRefuse) -> dict[str, Any]:
    return refuse_dict(refuse)


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

    return _refuse(ClassifyRefuse(code="unsupported_target", reason="unresolved adapter"))


def execute_steer(
    *,
    target: str,
    text: str,
    voice: Voice = "self",
    grant: str | None = None,
    authorization_source: str | None = None,
    scope: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    cached = idempotency.lookup(target=target, idempotency_key=idempotency_key)
    if cached is not None:
        return {**cached, "duplicate": True}

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

    if isinstance(resolved, DisplayEightTarget):
        result = display_adapter.steer_display_sync(
            resolved,
            text=text,
            voice=voice_s,
            grant=grant,
            authorization_source=authorization_source,
            scope=scope,
        )
    elif isinstance(resolved, CoworkMaestroTarget):
        result = cowork_adapter.followup(
            prompt_text=text,
            parent_thread=resolved.parent_thread,
        )
    elif isinstance(resolved, CoworkUrlTarget):
        result = cowork_adapter.followup(prompt_text=text, chat_url=resolved.url)
    else:
        return _refuse(ClassifyRefuse(code="unsupported_target", reason="unresolved adapter"))

    if result.get("ok") and idempotency_key:
        idempotency.remember(
            target=target, idempotency_key=idempotency_key, receipt=result
        )
    return result


__all__ = ["execute_read", "execute_steer"]
