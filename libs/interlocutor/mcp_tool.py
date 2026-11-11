"""MCP interlocutor tool — read, steer, list, spawn, and retire."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from interlocutor.facade import (
    execute_list,
    execute_read,
    execute_retire,
    execute_spawn,
    execute_steer,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _record(signal: str, **payload: Any) -> None:
    try:
        from mcp_events import record
    except ImportError:
        return
    record(signal, **payload)


def register_interlocutor_tool(mcp: FastMCP) -> None:
    """Register the interlocutor facade on both MCP surfaces."""

    @mcp.tool(title="Interlocutor")
    def interlocutor(
        op: Literal["read", "steer", "list", "spawn", "retire"],
        target: str,
        coverage: Literal["latest", "from_turn", "full", "meta"] = "latest",
        after_turn: int | None = None,
        limit: int = 10,
        text: str | None = None,
        text_uri: str | None = None,
        voice: Literal["self", "operator_relay"] = "self",
        grant: Literal["operator", "explicit"] | None = None,
        authorization_source: str | None = None,
        scope: str | None = None,
        idempotency_key: str | None = None,
        name: str | None = None,
        description: str | None = None,
        hidden: bool | None = None,
        mailbox: str | None = None,
        thread_scope: str | None = None,
        include_hidden: bool = True,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        """Read or steer an interlocutor target (Cowork, display :8/:9/:10, or Grok roster ops).

        ``list`` / ``spawn`` accept ``display:9``, ``display:10``, or ``display:9/grok``.
        ``retire`` requires ``display:<n>/<agent_id>`` on Grok displays 9 or 10.
        """
        if op == "read":
            result = execute_read(
                target=target,
                coverage=coverage,
                after_turn=after_turn,
                limit=limit,
            )
            outcome = "refused" if result.get("ok") is False else "completed"
            _record(f"interlocutor.read.{outcome}", target=target, coverage=coverage)
            return {**result, "op": "read", "target": target}

        if op == "list":
            result = execute_list(
                target=target,
                include_hidden=include_hidden,
                include_retired=include_retired,
            )
            outcome = "refused" if result.get("ok") is False else "completed"
            _record(f"interlocutor.list.{outcome}", target=target)
            return {**result, "op": "list", "target": target}

        if op == "spawn":
            if not (name or "").strip():
                refuse = {
                    "ok": False,
                    "code": "name_required",
                    "reason": "spawn requires name",
                }
                _record(
                    "interlocutor.spawn.refused", target=target, code="name_required"
                )
                return {**refuse, "op": "spawn", "target": target}
            result = execute_spawn(
                target=target,
                name=str(name),
                description=str(description or ""),
                hidden=True if hidden is None else bool(hidden),
                mailbox=mailbox,
                thread_scope=thread_scope,
                text=text,
                text_uri=text_uri,
                voice=voice,
                grant=grant,
                authorization_source=authorization_source,
                idempotency_key=idempotency_key,
            )
            outcome = "refused" if result.get("ok") is False else "completed"
            if result.get("ok") is False:
                _record(
                    "interlocutor.spawn.refused",
                    target=target,
                    code=result.get("code"),
                    reason=result.get("reason"),
                )
            else:
                _record(f"interlocutor.spawn.{outcome}", target=target)
            return {**result, "op": "spawn", "target": target}

        if op == "retire":
            result = execute_retire(target=target)
            outcome = "refused" if result.get("ok") is False else "completed"
            _record(f"interlocutor.retire.{outcome}", target=target)
            return {**result, "op": "retire", "target": target}

        if not (text or "").strip() and not (text_uri or "").strip():
            refuse = {
                "ok": False,
                "code": "text_required",
                "reason": "steer requires text or text_uri",
            }
            _record("interlocutor.steer.refused", target=target, code="text_required")
            return {**refuse, "op": "steer", "target": target}

        result = execute_steer(
            target=target,
            text=str(text or ""),
            text_uri=text_uri,
            voice=voice,
            grant=grant,
            authorization_source=authorization_source,
            scope=scope,
            idempotency_key=idempotency_key,
        )
        if result.get("duplicate"):
            _record("interlocutor.steer.duplicate", target=target)
        elif result.get("ok") is True:
            _record("interlocutor.steer.committed", target=target, voice=voice)
        elif result.get("ok") is False:
            _record(
                "interlocutor.steer.refused",
                target=target,
                code=result.get("code"),
            )
        else:
            _record("interlocutor.steer.unknown", target=target)
        return {**result, "op": "steer", "target": target}


__all__ = ["register_interlocutor_tool"]
