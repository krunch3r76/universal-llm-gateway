from __future__ import annotations

import httpx

from ..config import ProviderConfig
from .anthropic import AnthropicAdapter
from .base import ProviderAdapter
from .openai_compatible import OpenAICompatibleAdapter


def create_provider_adapter(
    config: ProviderConfig, client: httpx.AsyncClient
) -> ProviderAdapter:
    # Provider names are canonicalized at runtime to keep config inputs tolerant.
    provider = config.provider.strip().lower()
    match provider:
        case "anthropic":
            return AnthropicAdapter(config=config, client=client)
        case _:
            return OpenAICompatibleAdapter(config=config, client=client)
