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
        purpose_points: list[str] | None = None,
        verbatim_required: list[int] | None = None,
        mode: str | None = None,
        prior_draft: str | None = None,
        purpose: str | None = None,
        audience: str | None = None,
        attested_sources: list[str] | None = None,
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
        optional_fields: dict[str, Any] = {
            "intake_answers": intake_answers,
            "fact_sources": fact_sources,
            "purpose_points": purpose_points,
            "verbatim_required": verbatim_required,
            "mode": mode,
            "prior_draft": prior_draft,
            "purpose": purpose,
            "audience": audience,
            "attested_sources": attested_sources,
        }
        for key, value in optional_fields.items():
            if value is not None:
                intake_body[key] = value
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
