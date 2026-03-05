"""
Model ID and alias resolution — sync and async variants.

Resolution order (from cheapest to most expensive):
1. optionsNs.KEY  — expand from runtime pipeline options (no I/O)
2. Registry lookup — domain-specific → root namespace (local I/O)
3. Cloud ref async — delegates to cloud_resolver (HTTP, only for cloud:// refs)
4. Passthrough     — full IDs and pipeline-as-service IDs bypass the registry

The sync path (_resolve_model_alias) is used wherever a resolved ID is already
available. The async path (_resolve_model_alias_async) is the default for
_call_model — it handles cloud refs without blocking the event loop.

FULL_ID_INDICATORS drives the heuristic that distinguishes unregistered full
model IDs (should pass through) from misspelled aliases (should fail fast).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..protocol import PipelineContext

if TYPE_CHECKING:
    from ..schemas import StepConfig

logger = get_logger(__name__)

# Indicators used to detect full model IDs vs aliases
FULL_ID_INDICATORS = frozenset(
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


def _looks_like_full_model_id(model_id: str) -> bool:
    """
    Heuristic to detect if model_id is a full ID vs an alias.

    Full IDs typically contain:
    - Provider prefix separators (vendor/model)
    - Multiple hyphens (segmented naming)
    - Version indicators (instruct, chat, base)
    - Quantization markers (q4, q6, q8, f16)
    - Context length (8192, 32768, 131072)
    - Deployment markers (cpu, gpu, hybrid)
    - Variant markers (uncensored)

    Design Decision: Models without these indicators (e.g., "hermes-3-llama-3.1-8b")
    will be treated as aliases and fail if not registered. This is intentional —
    unregistered model IDs should fail fast rather than silently pass through
    and fail later at federation routing.

    Examples:
        "phi" → False (alias)
        "phi-3-5-mini-instruct-q4-k-m-32768-cpu" → True (full ID)
        "qwen2-5-7b-instruct-q4-k-m-32768-cpu" → True (full ID)
        "hermes3-llama-3.1-70b-uncensored-16384-hybrid" → True (full ID)
        "hermes-3-llama-3.1-8b" → False (no indicators, treated as alias)
    """
    # Aliases are typically short, single words or underscored
    if "_" in model_id and "-" not in model_id:
        return False  # Underscored aliases like "llama_3_1_8b"

    # Provider-prefixed IDs (e.g., "google/gemma-2-9b-it", "qwen/qwen-2.5-7b")
    # are full model IDs, not registry aliases.
    if "/" in model_id:
        return True

    # Full IDs have multiple segments
    segments = model_id.split("-")
    if len(segments) < 3:
        return False

    # Look for keyword indicators (quantization, deployment, variant)
    if any(seg.lower() in FULL_ID_INDICATORS for seg in segments):
        return True

    # Look for numeric context length (4-6 digit numbers: 2048, 8192, 32768, 131072)
    if any(seg.isdigit() and 1000 <= int(seg) <= 999999 for seg in segments):
        return True

    return False


def _resolve_model_pool(
    step: StepConfig,
    context: PipelineContext,
    *,
    exclude: str | None = None,
) -> list[str]:
    """Resolve model_pool domain field to a list of model aliases.

    Reads step's ``model_pool`` domain field (via model_extra), which may be:
    - list[str]: literal alias list — used directly
    - "optionsNs.KEY" or "KEY": resolved via pipeline options
    - None: returns []

    Args:
        step: Current step config.
        context: Pipeline execution context.
        exclude: Alias to remove from the pool (e.g. the originator model).

    Returns:
        List of resolved model aliases (may be empty).
    """
    pool = step.get_domain_field("model_pool")

    if pool is None:
        return []

    if isinstance(pool, list):
        aliases = list(pool)
    elif isinstance(pool, str):
        key = pool.removeprefix("optionsNs.")
        resolved = (context.options or {}).get(key, [])
        if not isinstance(resolved, list):
            logger.error(
                "Step '%s': model_pool option '%s' is not a list: %r",
                step.id,
                key,
                resolved,
            )
            return []
        aliases = list(resolved)
    else:
        logger.error("Step '%s': unexpected model_pool type: %r", step.id, pool)
        return []

    if exclude:
        aliases = [a for a in aliases if a != exclude]

    return aliases


def _resolve_model_alias(
    model_id: str,
    context: PipelineContext,
) -> str:
    """
    Resolve model alias to full ID via registry.

    Resolution order: optionsNs binding → domain-specific → root → passthrough

    Args:
        model_id: Alias (e.g., "phi"), optionsNs binding, or full ID
        context: Pipeline context with registry

    Returns:
        Full model ID

    Raises:
        KeyError: If alias not found and doesn't appear to be full ID
        ValueError: If optionsNs binding references missing/invalid option
    """
    if model_id.startswith("optionsNs."):
        key = model_id[len("optionsNs.") :]
        resolved = (context.options or {}).get(key)
        if not resolved or not isinstance(resolved, str):
            raise ValueError(
                f"model_ref '{model_id}' references optionsNs.{key} "
                f"but no string value found in pipeline options"
            )
        model_id = resolved

    registry = context._registry
    domain = context.pipeline.domain

    try:
        model_config = registry.get_model_config(
            model_id,
            domain=domain,
            search_path=context.pipeline.source_search_path,
        )
        resolved = model_config.model
        if resolved != model_id:
            logger.debug(f"Resolved model alias: {model_id} → {resolved}")
        return resolved
    except KeyError:
        # Pipeline IDs are callable virtual models (pipeline-as-service).
        # Treat them as full IDs: bypass alias lookup and let routing handle them.
        if registry.is_pipeline(model_id):
            logger.debug(f"Model '{model_id}' is a pipeline ID, using as-is")
            return model_id

        if _looks_like_full_model_id(model_id):
            logger.debug(f"Model '{model_id}' not in registry, using as full ID")
            return model_id

        logger.error(
            f"Model alias '{model_id}' not found in registry "
            f"(domain={domain}). Check pipelines.local/models.yaml or "
            f"pipelines.local/{domain}/models.yaml"
        )
        raise


def _get_cloud_select_fn(
    context: PipelineContext,
) -> Callable[[dict[str, Any]], Any] | None:
    """Return the cloud model-selection callable, or None if unavailable.

    Traverses the optional federation chain (proxy → federation_integration →
    forwarder → cloud_forwarder) defensively via getattr so that pipelines
    running without a cloud proxy (e.g., pure-local inference, tests) degrade
    gracefully rather than raising AttributeError.

    The returned async callable wraps cloud_forwarder.select_models so callers
    don't need to know the internal forwarder structure.
    """
    proxy = getattr(context, "_proxy", None)
    if not proxy:
        return None
    fed = getattr(proxy, "federation_integration", None)
    if not fed:
        return None
    fwd = getattr(fed, "forwarder", None)
    if not fwd:
        return None
    client = getattr(fwd, "cloud_forwarder", None)
    if not client or not hasattr(client, "select_models"):
        return None

    async def _select(payload: dict[str, Any]) -> dict[str, Any]:
        return await client.select_models(payload)

    return _select


def _get_cloud_proxy_mode(context: PipelineContext) -> str:
    """Return the cloud proxy transport mode string, or 'unknown' if unavailable.

    Included in CloudModelResolved/Failed events so the pipeline viewer can
    surface whether requests went via Unix socket (uds) or TCP — useful when
    diagnosing latency differences between local and remote cloud proxy setups.
    """
    proxy = getattr(context, "_proxy", None)
    fed = getattr(proxy, "federation_integration", None) if proxy else None
    fwd = getattr(fed, "forwarder", None) if fed else None
    client = getattr(fwd, "cloud_forwarder", None) if fwd else None
    return getattr(client, "proxy_mode", "unknown") if client else "unknown"


async def _resolve_model_alias_async(
    model_id: str,
    context: PipelineContext,
    *,
    step_name: str = "",
) -> str:
    """Async alias resolver — handles cloud refs without blocking the event loop.

    Extends the sync resolution path with a cloud ref branch: if the model_id
    matches the cloud:// prefix pattern, it delegates to resolve_cloud_ref_async
    (which calls the cloud proxy's /api/select endpoint) and emits a
    CloudModelResolved or CloudModelResolutionFailed pipeline event.

    Falls through to _resolve_model_alias for all non-cloud refs so the two
    paths share optionsNs expansion and registry lookup logic.

    Args:
        step_name: Forwarded to CloudModel* events for pipeline viewer linkage.
    """
    from ...events.inference import CloudModelResolutionFailed, CloudModelResolved
    from ...execution.cloud_resolver import is_cloud_ref, resolve_cloud_ref_async

    # Reuse existing optionsNs expansion logic.
    if model_id.startswith("optionsNs."):
        key = model_id[len("optionsNs.") :]
        resolved = (context.options or {}).get(key)
        if not resolved or not isinstance(resolved, str):
            raise ValueError(
                f"model_ref '{model_id}' references optionsNs.{key} "
                f"but no string value found in pipeline options"
            )
        model_id = resolved

    if is_cloud_ref(model_id):
        cloud_select_fn = _get_cloud_select_fn(context)
        resolved, candidate_count = await resolve_cloud_ref_async(
            model_id,
            cloud_select_fn=cloud_select_fn,
        )
        cloud_proxy_mode = _get_cloud_proxy_mode(context)
        recorder = context.recorder
        if resolved is not None:
            if recorder:
                recorder.emit(
                    CloudModelResolved(
                        step_name=step_name,
                        requested_ref=model_id,
                        resolved_model_id=resolved,
                        cloud_proxy_mode=cloud_proxy_mode,
                        candidate_count=candidate_count,
                    )
                )
            return resolved

        if recorder:
            recorder.emit(
                CloudModelResolutionFailed(
                    step_name=step_name,
                    requested_ref=model_id,
                    cloud_proxy_mode=cloud_proxy_mode,
                    reason="no_candidates_or_proxy_unavailable",
                )
            )
        raise KeyError(f"cloud resolver returned no models for '{model_id}'")

    return _resolve_model_alias(model_id, context)
