"""
Native-provider ingress boundary (cloud proxy + Stargate /api/v1/providers/...).

Contract:
- Clients on provider-native routes send **provider API JSON** (e.g. Anthropic
  Messages shape) and **raw provider model IDs** (e.g. ``claude-sonnet-4-20250514``).
- We derive a **workspace-style catalog id** ``{provider}/{raw}`` for telemetry
  and internal ``ModelId`` alignment with the OpenAI-compatible path.
- The **upstream HTTP body** is forwarded **as-is** except the adapter may add
  auth headers; ``model`` in the body stays the raw provider string.

¬ Re-parse or strip model IDs here beyond ``ModelId.parse`` for identity —
``api_model_id`` on the parsed id matches bare Anthropic/xAI upstream names.
"""

from __future__ import annotations

from model_id import ModelId

# Path segment keys under ``/api/v1/providers/{key}/...``
NATIVE_PROVIDER_KEYS = frozenset({"anthropic", "xai", "openai", "google"})

# xAI effort-tier encoding — suffix appended to model ID out-of-band so that
# callers (grok CLI, grokbuild) that cannot pass reasoning.effort directly can
# encode the desired tier in the stanza model name.
# Shared by native_routes, cloud_proxy, and catalog synthesis.
_EFFORT_SUFFIX = "__effort_"
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh")


def workspace_catalog_id_from_native(provider_key: str, raw_model_id: str) -> str:
    """Map (endpoint provider, raw model field) → workspace id string (``anthropic/...``)."""
    p = provider_key.strip().lower()
    raw = (raw_model_id or "").strip()
    if not raw:
        return raw
    if "/" in raw:
        return ModelId.parse(raw).original
    return f"{p}/{raw}"


def model_id_from_native(provider_key: str, raw_model_id: str) -> ModelId:
    """Parse workspace catalog id for routing/telemetry (same as /v1/chat/completions)."""
    return ModelId.parse(workspace_catalog_id_from_native(provider_key, raw_model_id))


def raw_model_from_native_body(provider_key: str, body: dict) -> str:
    """Read model field from native JSON (Anthropic/xAI Responses both use ``model``)."""
    _ = provider_key  # reserved for provider-specific keys later
    return str(body.get("model", ""))
