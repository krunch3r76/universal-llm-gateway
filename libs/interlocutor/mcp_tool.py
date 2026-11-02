"""MCP interlocutor tool — read and steer only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from interlocutor.facade import execute_read, execute_steer

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
        op: Literal["read", "steer"],
        target: str,
        coverage: Literal["latest", "from_turn", "full", "meta"] = "latest",
        after_turn: int | None = None,
        limit: int = 10,
        text: str | None = None,
        voice: Literal["self", "operator_relay"] = "self",
        grant: Literal["operator", "explicit"] | None = None,
        authorization_source: str | None = None,
        scope: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Read or steer an interlocutor target (Cowork cse_session or display :8).

        ``read`` harvests via ``cse_session`` for Cowork targets or reads the latest
        composer turn on display ``:8``. ``after_turn`` is forwarded to harvest
        unchanged (no off-by-one adjustment).

        ``steer`` sends followup on Cowork or pastes on display ``:8``.
        ``voice=operator_relay`` composes the Kaywan operator watermark; ``voice=self``
        does not watermark.
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

        if not (text or "").strip():
            refuse = {
                "ok": False,
                "code": "text_required",
                "reason": "steer requires text",
            }
            _record("interlocutor.steer.refused", target=target, code="text_required")
            return {**refuse, "op": "steer", "target": target}

        result = execute_steer(
            target=target,
            text=str(text),
            voice=voice,
            grant=grant,
            authorization_source=authorization_source,
            scope=scope,
            idempotency_key=idempotency_key,
        )
        if result.get("duplicate"):
            _record("interlocutor.steer.duplicate", target=target)
        elif result.get("ok") is False:
            _record(
                "interlocutor.steer.refused",
                target=target,
                code=result.get("code"),
            )
        else:
            _record("interlocutor.steer.committed", target=target, voice=voice)
        return {**result, "op": "steer", "target": target}


__all__ = ["register_interlocutor_tool"]
