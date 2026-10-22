"""Typed MCP ``outbound_write`` intake relay (Axis 5C)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from writing_intake.schema import OutboundWriteIntake

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_outbound_write_tool(mcp: FastMCP) -> None:
    from tools.pipeline import _capability_async, _pipeline_run

    @mcp.tool(title="Outbound Write")
    def outbound_write(
        verbatim_notes: list[str],
        recipient: str,
        objective: str,
        channel: Literal["email", "sms", "letter", "portal", "filing"],
        max_chars: int | None = None,
        not_attested: list[str] | None = None,
        working_set: list[str] | None = None,
        intake_answers: list[str] | None = None,
        fact_sources: dict[str, str] | list[dict[str, Any]] | None = None,
        async_exec: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        intake_body: dict[str, Any] = {
            "verbatim_notes": verbatim_notes,
            "recipient": recipient,
            "objective": objective,
            "channel": channel,
            "max_chars": max_chars,
            "not_attested": not_attested or [],
            "working_set": working_set or [],
        }
        if intake_answers is not None:
            intake_body["intake_answers"] = intake_answers
        if fact_sources is not None:
            intake_body["fact_sources"] = fact_sources
        intake = OutboundWriteIntake.model_validate(intake_body)
        options = {"intake": intake.model_dump(exclude_none=True)}
        pipeline_id = "writing/writer-specialist-v1"
        if async_exec:
            return _capability_async(pipeline_id, options, messages=[])
        return _pipeline_run(
            "writer-specialist-v1",
            messages=[],
            options=options,
            timeout=timeout,
        )
