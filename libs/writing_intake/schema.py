"""Pydantic intake model for outbound_write and writing assemble."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ChannelType = Literal["email", "sms", "letter", "portal", "filing"]
IntakeMode = Literal["standard", "fact_inventory"]

_INTAKE_SCHEMA_HINT: dict[str, Any] = {
    "required_fields": ["verbatim_notes", "recipient", "objective", "channel"],
    "optional_fields": [
        "mode",
        "max_chars",
        "not_attested",
        "working_set",
        "signer",
        "voice_anchor",
        "standing_limits",
        "purpose_points",
        "verbatim_required",
        "intake_answers",
        "fact_sources",
    ],
    "call_shape": (
        "Structured intake via pipeline_options.intake (or top-level intake fields). "
        "messages may be omitted, empty, or non-empty when structured intake is present; "
        "non-empty messages are refused only when no structured intake block is provided. "
        "options.messages or options.prompt are always refused."
    ),
    "fact_inventory_fields": [
        "mode='fact_inventory'",
        "prior_draft",
        "purpose (or objective)",
        "audience (or recipient)",
        "attested_sources (optional)",
        "channel (default 'filing')",
    ],
    "minimal_example": {
        "standard": {
            "mode": "standard",
            "verbatim_notes": ["The lease ended March 1, 2026 (Exhibit 2)."],
            "recipient": "Housing authority clerk",
            "objective": "Request reinstatement hearing date",
            "channel": "filing",
        },
        "fact_inventory": {
            "mode": "fact_inventory",
            "prior_draft": "I declare… Tenant paid rent (Exhibit 2).",
            "purpose": "Reinstatement declaration for AAB",
            "audience": "Administrative law judge",
            "attested_sources": ["Exhibit 2 notice"],
            "channel": "filing",
        },
    },
}


def _refusal(refused: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "refused": refused,
        "error": error,
        **_INTAKE_SCHEMA_HINT,
    }


class OutboundWriteIntake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: IntakeMode = "standard"
    verbatim_notes: list[str] | None = None
    recipient: str | None = None
    objective: str | None = None
    channel: ChannelType | None = None
    max_chars: int | None = None
    not_attested: list[str] = Field(default_factory=list)
    working_set: list[str] = Field(default_factory=list)
    signer: str | None = None
    voice_anchor: str | None = None
    standing_limits: str | None = None
    prior_draft: str | None = None
    purpose: str | None = None
    audience: str | None = None
    attested_sources: list[str] = Field(default_factory=list)
    purpose_points: list[str] = Field(default_factory=list)
    verbatim_required: list[int] = Field(default_factory=list)
    intake_answers: list[str] = Field(default_factory=list)
    fact_sources: dict[str, str] = Field(default_factory=dict)

    @field_validator("fact_sources", mode="before")
    @classmethod
    def _coerce_fact_sources_field(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return {str(key): str(item) for key, item in value.items()}
        if isinstance(value, list):
            coerced: dict[str, str] = {}
            for row in value:
                if not isinstance(row, dict):
                    continue
                claim = row.get("claim") or row.get("match") or row.get("text")
                provenance = row.get("provenance") or row.get("source")
                if claim and provenance:
                    coerced[str(claim)] = str(provenance)
            return coerced
        return {}

    @field_validator("purpose_points")
    @classmethod
    def _purpose_points_clean(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @model_validator(mode="after")
    def _normalize_aliases_and_mode(self) -> OutboundWriteIntake:
        if self.objective is None and self.purpose:
            self.objective = self.purpose.strip()
        if self.purpose is None and self.objective:
            self.purpose = self.objective.strip()
        if self.recipient is None and self.audience:
            self.recipient = self.audience.strip()
        if self.audience is None and self.recipient:
            self.audience = self.recipient.strip()
        if self.mode == "fact_inventory":
            if self.channel is None:
                self.channel = "filing"
            if not (self.prior_draft and str(self.prior_draft).strip()):
                raise ValueError("fact_inventory requires non-empty prior_draft")
            if not (self.objective and str(self.objective).strip()):
                raise ValueError("fact_inventory requires purpose or objective")
            if not (self.recipient and str(self.recipient).strip()):
                raise ValueError("fact_inventory requires audience or recipient")
            return self
        if not self.verbatim_notes:
            raise ValueError(
                "verbatim_notes must contain at least one non-empty string"
            )
        notes_len = len(self.verbatim_notes or [])
        for index in self.verbatim_required:
            if index < 0 or index >= notes_len:
                raise ValueError("verbatim_required index out of range")
        if self.channel is None:
            raise ValueError("channel is required")
        return self

    @field_validator("verbatim_notes")
    @classmethod
    def _verbatim_non_empty(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError(
                "verbatim_notes must contain at least one non-empty string"
            )
        return cleaned

    @field_validator("recipient", "objective", "purpose", "audience")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value or "").strip()
        return text or None

    @field_validator("max_chars")
    @classmethod
    def _max_chars_positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("max_chars must be > 0 when set")
        return value


def _structured_intake_raw(options: Mapping[str, Any]) -> dict[str, Any]:
    intake_raw = options.get("intake")
    if isinstance(intake_raw, str):
        try:
            parsed_intake = json.loads(intake_raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed_intake, dict):
            return dict(parsed_intake)
        return {}
    if isinstance(intake_raw, dict):
        return dict(intake_raw)
    raw = dict(intake_raw or {})
    if not raw and (
        "verbatim_notes" in options or options.get("mode") == "fact_inventory"
    ):
        for key in (
            "mode",
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
            "prior_draft",
            "purpose",
            "audience",
            "attested_sources",
            "purpose_points",
            "verbatim_required",
            "intake_answers",
            "fact_sources",
        ):
            if key in options:
                raw[key] = options[key]
    return raw


def validate_intake_payload(
    options: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[OutboundWriteIntake | None, dict[str, Any] | None]:
    """Validate structured intake; refuse raw prompt / messages."""
    if "prompt" in options or "messages" in options:
        return None, _refusal(
            "raw_prompt_prohibited_use_intake_schema",
            "messages/prompt prohibited; pass structured intake fields",
        )
    intake_raw = options.get("intake")
    if isinstance(intake_raw, str):
        try:
            parsed_intake = json.loads(intake_raw)
        except json.JSONDecodeError as exc:
            return None, _refusal(
                "intake_invalid", f"intake JSON parse failed: {exc.msg}"
            )
        if not isinstance(parsed_intake, dict):
            return None, _refusal("intake_invalid", "intake must be a JSON object")
        raw = dict(parsed_intake)
    else:
        raw = _structured_intake_raw(options)
    if messages:
        non_empty = [
            m
            for m in messages
            if isinstance(m, dict) and str(m.get("content") or "").strip()
        ]
        if non_empty and not raw:
            return None, _refusal(
                "raw_prompt_prohibited_use_intake_schema",
                "messages/prompt prohibited; pass structured intake fields",
            )
    if raw.get("mode") != "fact_inventory" and "verbatim_notes" not in raw:
        return None, None
    if raw.get("mode") == "fact_inventory" and "prior_draft" not in raw:
        return None, _refusal(
            "intake_invalid",
            "fact_inventory requires prior_draft",
        )
    try:
        intake = OutboundWriteIntake.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — surface validation to caller
        return None, _refusal("intake_invalid", str(exc))
    return intake, None
