"""Shared outbound-write intake schema (Axis 5C)."""

from writing_intake.schema import (
    ChannelType,
    OutboundWriteIntake,
    validate_intake_payload,
)

__all__ = [
    "ChannelType",
    "OutboundWriteIntake",
    "validate_intake_payload",
]
