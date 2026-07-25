"""Resolve bare cloud model IDs to provider-prefixed wire form.

Single source of truth for MCP ``team_dispatch``, Stargate frontier/team HTTP
dispatch, and any path
that routes through ``ModelId`` + ``effective_provider_for_model``. Bare IDs
like ``gpt-5.5`` must not fall through to a default provider (historically
Anthropic) — they are either prefixed here or rejected pre-dispatch.

Agent substrates (``cursor/``, ``cdp/``) are first-class peers — never
``Unknown provider``. Cloud-only call sites must raise
:class:`SubstrateCapabilityUnimplementedError` when they cannot serve those
backends.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model_id import ROUTING_PREFIXES, ModelId, infer_cloud_provider_from_bare

# Direct native providers (see cloud-model-routing_ws.mdc).
KNOWN_CLOUD_PROVIDERS = frozenset(
    {"openai", "anthropic", "xai", "google", "chatgpt"}
)

# Async agent substrates — peer spawn endpoints, not cloud Messages/Responses.
# ``cursor`` → backend_type cursor_sdk; ``cdp`` → backend_type cdp.
AGENT_SUBSTRATE_PROVIDERS = frozenset({"cursor", "cdp"})

# Heuristic markers for local gateway model IDs (not cloud aliases).
_LOCAL_ID_INDICATORS = frozenset(
    {
        "instruct",
        "chat",
        "base",
        "q4",
        "q8",
        "q6",
        "f16",
        "cpu",
        "gpu",
        "hybrid",
        "uncensored",
    }
)


@dataclass(frozen=True, slots=True)
class WireModelResolution:
    """Outcome of ``resolve_wire_model_id``."""

    wire_id: str
    provider: str | None
    was_bare: bool
    backend_type: str | None = None


class WireModelResolutionError(ValueError):
    """Pre-dispatch rejection for ambiguous or unknown model IDs."""

    def __init__(self, model_id: str, reason: str) -> None:
        self.model_id = model_id
        self.reason = reason
        super().__init__(reason)


class SubstrateCapabilityUnimplementedError(ValueError):
    """Known substrate cannot serve the requested capability yet.

    Prefer this over treating ``cursor/`` or ``cdp/`` as unknown providers.
    """

    def __init__(
        self,
        *,
        substrate: str,
        capability: str,
        reason: str,
        model_id: str | None = None,
    ) -> None:
        self.substrate = substrate
        self.capability = capability
        self.reason = reason
        self.model_id = model_id
        msg = (
            f"substrate_capability_unimplemented: substrate={substrate!r} "
            f"capability={capability!r} — {reason}"
        )
        if model_id:
            msg = f"{msg} (model={model_id!r})"
        super().__init__(msg)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": "substrate_capability_unimplemented",
            "substrate": self.substrate,
            "capability": self.capability,
            "reason": self.reason,
            "model_id": self.model_id,
        }


def _strip_effort_suffix(model_id: str) -> str:
    """Remove cloud-proxy ``__effort_*`` suffix before alias inference."""
    if "__effort_" in model_id:
        return model_id.split("__effort_", 1)[0]
    return model_id


def _looks_like_local_model_id(model_id: str) -> bool:
    if "_" in model_id and "-" not in model_id:
        return True
    segments = model_id.split("-")
    if len(segments) >= 3 and any(
        seg.lower() in _LOCAL_ID_INDICATORS for seg in segments
    ):
        return True
    if any(seg.isdigit() and 1000 <= int(seg) <= 999_999 for seg in segments):
        return True
    return False


def _validate_prefixed_id(model_id: str) -> WireModelResolution:
    parsed = ModelId.parse(model_id)
    routing_layer = parsed.routing_layer
    if routing_layer is not None and routing_layer in ROUTING_PREFIXES:
        return WireModelResolution(
            wire_id=model_id,
            provider=routing_layer,
            was_bare=False,
            backend_type=parsed.backend_type,
        )
    if parsed.provider is None:
        raise WireModelResolutionError(
            model_id,
            (
                f"Model {model_id!r} has no recognized cloud provider prefix. "
                f"Use provider/model form (e.g. openai/gpt-5.5, google/gemini-3.6-flash)."
            ),
        )
    provider = parsed.provider.lower()
    if provider in AGENT_SUBSTRATE_PROVIDERS:
        _, _, picker = model_id.partition("/")
        if not picker.strip():
            raise WireModelResolutionError(
                model_id,
                (
                    f"Agent substrate id {model_id!r} requires a non-empty picker "
                    f"(e.g. cdp/opus-5, cursor/claude-opus-5)."
                ),
            )
        return WireModelResolution(
            wire_id=model_id,
            provider=provider,
            was_bare=False,
            backend_type=parsed.backend_type,
        )
    if provider not in KNOWN_CLOUD_PROVIDERS:
        raise WireModelResolutionError(
            model_id,
            (
                f"Unknown provider prefix {provider!r} in {model_id!r}. "
                f"Known providers: {sorted(KNOWN_CLOUD_PROVIDERS)}; "
                f"agent substrates: {sorted(AGENT_SUBSTRATE_PROVIDERS)}."
            ),
        )
    return WireModelResolution(
        wire_id=model_id,
        provider=provider,
        was_bare=False,
        backend_type=parsed.backend_type or "cloud_api",
    )


def resolve_wire_model_id(
    model_id: str,
    *,
    require_cloud: bool = False,
) -> WireModelResolution:
    """Normalize a model id for frontier/cloud/agent-substrate dispatch wiring.

    Args:
        model_id: Caller-supplied id (bare or ``provider/model``).
        require_cloud: When True, reject ids that look like local gateway models.
            Agent substrates (``cursor/``, ``cdp/``) are still admitted — they
            are not local gateway ids.

    Returns:
        WireModelResolution with prefixed ``wire_id`` when inference applied.

    Raises:
        WireModelResolutionError: Unknown bare id, missing provider, or local id
            when ``require_cloud`` is set.
    """
    raw = (model_id or "").strip()
    if not raw:
        raise WireModelResolutionError(model_id, "Model ID cannot be empty")

    work = _strip_effort_suffix(raw)

    if "/" in work:
        return _validate_prefixed_id(work)

    # Bare cloud families before local-gateway heuristics so cloud suffixes
    # like ``gpt-5-chat`` / ``grok-4-base`` are not misclassified as local ids.
    provider = infer_cloud_provider_from_bare(work)
    if provider is not None:
        wire_id = f"{provider}/{work}"
        return WireModelResolution(
            wire_id=wire_id,
            provider=provider,
            was_bare=True,
            backend_type="cloud_api",
        )

    if _looks_like_local_model_id(work):
        if require_cloud:
            raise WireModelResolutionError(
                work,
                (
                    f"Model {work!r} looks like a local gateway id, not a cloud "
                    "frontier model. Use provider/model (e.g. openai/gpt-5.5)."
                ),
            )
        return WireModelResolution(
            wire_id=work, provider=None, was_bare=False, backend_type=None
        )

    raise WireModelResolutionError(
        work,
        (
            f"Bare model id {work!r} cannot be routed — prefix with provider "
            f"(e.g. openai/{work}, anthropic/{work}). Known bare families: "
            "gpt-*, claude-*, grok-*, gemini-*."
        ),
    )


def require_cloud_api_backend(
    resolution: WireModelResolution,
    *,
    capability: str,
) -> WireModelResolution:
    """Admit only cloud_api backends; raise for cursor/cdp with a clear code."""
    backend = resolution.backend_type
    provider = (resolution.provider or "").lower()
    if backend in {"cursor_sdk", "cdp"} or provider in AGENT_SUBSTRATE_PROVIDERS:
        substrate = "cdp" if backend == "cdp" or provider == "cdp" else "cursor"
        raise SubstrateCapabilityUnimplementedError(
            substrate=substrate,
            capability=capability,
            reason=(
                f"{capability} is cloud-API-only; use team_dispatch/spawn "
                f"(model={resolution.wire_id!r}) for async agent substrates"
            ),
            model_id=resolution.wire_id,
        )
    return resolution


def require_cloud_provider(
    parsed_provider: str | None,
    *,
    model: str,
) -> str:
    """Return lowercase provider or raise — no implicit default provider."""
    if parsed_provider:
        return parsed_provider.strip().lower()
    raise WireModelResolutionError(
        model,
        (
            f"Model {model!r} has no provider prefix. Use provider/model form "
            "(e.g. openai/gpt-5.5) or pass a bare id that matches a known family "
            "(gpt-*, claude-*, grok-*, gemini-*)."
        ),
    )
