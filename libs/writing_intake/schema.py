"""Pydantic intake model for outbound_write and writing assemble."""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

ChannelType = Literal["email", "sms", "letter", "portal", "filing"]


class OutboundWriteIntake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verbatim_notes: list[str]
    recipient: str
    objective: str
    channel: ChannelType
    max_chars: int | None = None
    not_attested: list[str] = Field(default_factory=list)
    working_set: list[str] = Field(default_factory=list)
    signer: str | None = None
    voice_anchor: str | None = None
    standing_limits: str | None = None

    @field_validator("verbatim_notes")
    @classmethod
    def _verbatim_non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("verbatim_notes must contain at least one non-empty string")
        return cleaned

    @field_validator("recipient", "objective")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("field must be non-empty")
        return text

    @field_validator("max_chars")
    @classmethod
    def _max_chars_positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("max_chars must be > 0 when set")
        return value


def validate_intake_payload(
    options: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[OutboundWriteIntake | None, dict[str, Any] | None]:
    """Validate structured intake; refuse raw prompt / messages."""
    if messages:
        non_empty = [
            m
            for m in messages
            if isinstance(m, dict) and str(m.get("content") or "").strip()
        ]
        if non_empty:
            return None, {
                "ok": False,
                "refused": "raw_prompt_prohibited_use_intake_schema",
                "error": "messages/prompt prohibited; pass structured intake fields",
            }
    if "prompt" in options or "messages" in options:
        return None, {
            "ok": False,
            "refused": "raw_prompt_prohibited_use_intake_schema",
            "error": "messages/prompt prohibited; pass structured intake fields",
        }
    raw = dict(options.get("intake") or {})
    if not raw:
        for key in (
            "verbatim_notes",
            "recipient",
            "objective",
            "channel",
            "max_chars",
            "not_attested",
            "working_set",
            "signer",
            "voice_anchor",
            "standing_limits",
        ):
            if key in options:
                raw[key] = options[key]
    if not raw:
        return None, {
            "ok": False,
            "refused": "intake_invalid",
            "error": "missing structured intake fields",
        }
    try:
        intake = OutboundWriteIntake.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — surface validation to caller
        return None, {
            "ok": False,
            "refused": "intake_invalid",
            "error": str(exc),
        }
    return intake, None
