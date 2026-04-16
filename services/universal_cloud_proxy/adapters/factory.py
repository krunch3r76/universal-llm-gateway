from __future__ import annotations

import httpx
from universal_event_bus import EventBus

from ..config import ProviderConfig
from .anthropic import AnthropicAdapter
from .base import ProviderAdapter
from .google import GoogleAdapter
from .openai_compatible import OpenAICompatibleAdapter


def create_provider_adapter(
    config: ProviderConfig,
    client: httpx.AsyncClient,
    *,
    event_bus: EventBus | None = None,
) -> ProviderAdapter:
    provider = config.provider.strip().lower()
    match provider:
        case "anthropic":
            return AnthropicAdapter(config=config, client=client, event_bus=event_bus)
        case "google":
            return GoogleAdapter(config=config, client=client, event_bus=event_bus)
        case _:
            return OpenAICompatibleAdapter(config=config, client=client)
