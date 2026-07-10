"""Frontier API model capability card — single reader for dispatch MCP surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from model_id import ModelId

__all__ = [
    "CARD_VERSION",
    "MODEL_CAPABILITY_CARDS",
    "CapabilityCardError",
    "ModelCapabilityCard",
    "capability_card",
    "inline_only",
    "mcp_capable",
    "mcp_client_tool_loop",
    "mcp_remote_connector",
    "server_side_tools",
    "skills_mount_backend",
]

CARD_VERSION: Final[str] = "2026-07-09"

ReasonCode = Literal["capability_card_missing", "capability_card_field_missing"]
SkillsMountBackend = Literal["openai_container", "none"]


@dataclass(frozen=True, slots=True)
class ModelCapabilityCard:
    mcp_client_tool_loop: bool | None
    mcp_remote_connector: bool | None
    server_side_tools: tuple[str, ...] | None
    skills_mount_backend: str | None


class CapabilityCardError(LookupError):
    """Raised when a model lacks a card or an interrogated field is unset."""

    def __init__(
        self,
        model: str,
        capability_field: str,
        reason_code: ReasonCode,
    ) -> None:
        self.model = model
        self.capability_field = capability_field
        self.reason_code = reason_code
        super().__init__(
            f"{reason_code}: model={model!r} capability_field={capability_field!r}"
        )


_ANTHROPIC_API = ModelCapabilityCard(
    mcp_client_tool_loop=True,
    mcp_remote_connector=True,
    server_side_tools=(),
    skills_mount_backend="none",
)

_OPENAI_API = ModelCapabilityCard(
    mcp_client_tool_loop=True,
    mcp_remote_connector=False,
    server_side_tools=(),
    skills_mount_backend="openai_container",
)

_XAI_STANDARD = ModelCapabilityCard(
    mcp_client_tool_loop=True,
    mcp_remote_connector=False,
    server_side_tools=("web_search", "x_search", "code_interpreter"),
    skills_mount_backend="none",
)

_XAI_MULTI_AGENT = ModelCapabilityCard(
    mcp_client_tool_loop=False,
    mcp_remote_connector=False,
    server_side_tools=("web_search", "x_search", "code_interpreter"),
    skills_mount_backend="none",
)

_GEMINI_API = ModelCapabilityCard(
    mcp_client_tool_loop=True,
    mcp_remote_connector=False,
    server_side_tools=(),
    skills_mount_backend="none",
)

MODEL_CAPABILITY_CARDS: Final[dict[str, ModelCapabilityCard]] = {
    "anthropic/claude-sonnet-4-6": _ANTHROPIC_API,
    "anthropic/claude-opus-4-8": _ANTHROPIC_API,
    "anthropic/claude-opus-4": _ANTHROPIC_API,
    "anthropic/claude-3-5-sonnet": _ANTHROPIC_API,
    "openai/gpt-5.6-sol": _OPENAI_API,
    "openai/gpt-5.6-terra": _OPENAI_API,
    "openai/gpt-5.6-luna": _OPENAI_API,
    "openai/gpt-5.5": _OPENAI_API,
    "openai/gpt-5.4": _OPENAI_API,
    "openai/gpt-5.4-mini": _OPENAI_API,
    "openai/o4-mini": _OPENAI_API,
    "openai/o3": _OPENAI_API,
    "xai/grok-4.5": _XAI_STANDARD,
    "xai/grok-4.20-0309-reasoning": _XAI_STANDARD,
    "xai/grok-4.20-multi-agent-0309": _XAI_MULTI_AGENT,
    "google/gemini-3.5-flash": _GEMINI_API,
    "google/gemini-3.1-pro-preview": _GEMINI_API,
    "google/gemini-2.5-flash": _GEMINI_API,
    "google/gemini-2.5-pro": _GEMINI_API,
    "google/gemini-2.5-flash-lite": _GEMINI_API,
}


def _normalized(model: str) -> str:
    return ModelId.parse(model).normalized


def capability_card(model: str) -> ModelCapabilityCard:
    """Return the card for ``model``; raise ``CapabilityCardError`` on miss."""
    key = _normalized(model)
    card = MODEL_CAPABILITY_CARDS.get(key)
    if card is None:
        raise CapabilityCardError(key, "card", "capability_card_missing")
    return card


def _require_bool(
    model: str, field: str, value: bool | None
) -> bool:
    if value is None:
        raise CapabilityCardError(model, field, "capability_card_field_missing")
    return value


def _require_tuple(
    model: str, field: str, value: tuple[str, ...] | None
) -> tuple[str, ...]:
    if value is None:
        raise CapabilityCardError(model, field, "capability_card_field_missing")
    return value


def _require_str(model: str, field: str, value: str | None) -> str:
    if value is None:
        raise CapabilityCardError(model, field, "capability_card_field_missing")
    return value


def mcp_client_tool_loop(model: str) -> bool:
    key = _normalized(model)
    return _require_bool(
        key, "mcp_client_tool_loop", capability_card(model).mcp_client_tool_loop
    )


def mcp_remote_connector(model: str) -> bool:
    key = _normalized(model)
    return _require_bool(
        key, "mcp_remote_connector", capability_card(model).mcp_remote_connector
    )


def mcp_capable(model: str) -> bool:
    return mcp_client_tool_loop(model) or mcp_remote_connector(model)


def inline_only(model: str) -> bool:
    return not mcp_capable(model)


def server_side_tools(model: str) -> tuple[str, ...]:
    key = _normalized(model)
    return _require_tuple(
        key, "server_side_tools", capability_card(model).server_side_tools
    )


def skills_mount_backend(model: str) -> str:
    key = _normalized(model)
    return _require_str(
        key, "skills_mount_backend", capability_card(model).skills_mount_backend
    )
