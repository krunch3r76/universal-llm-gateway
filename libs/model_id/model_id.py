"""
Core ModelId class for parsing and representing model identifiers.

Pattern: {base_model_id}[-{context}][-hybrid][-cpu]

Examples:
    - 'hermes3-llama-3.1-70b' → base only
    - 'hermes3-llama-3.1-70b-16384' → with context
    - 'hermes3-llama-3.1-70b-16384-hybrid' → hybrid (partial GPU)
    - 'hermes3-llama-3.1-70b-16384-cpu' → CPU mode
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Context length pattern: 3-6 digits (128 to 999999)
_CONTEXT_PATTERN = re.compile(r"-(\d{3,6})$")

# Known routing layer prefixes — tell the system HOW to reach the provider
_ROUTING_PREFIXES = frozenset({"openrouter"})

# Bare cloud id families (no ``provider/`` prefix) — first match wins.
BARE_CLOUD_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("o4-", "openai"),
    ("chatgpt-", "openai"),
    ("claude-", "anthropic"),
    ("grok-", "xai"),
    ("gemini-", "google"),
)


def infer_cloud_provider_from_bare(bare: str) -> str | None:
    """Infer cloud provider from a bare model id prefix, or None if unknown."""
    lower = bare.lower()
    for prefix, provider in BARE_CLOUD_PREFIX_RULES:
        if lower.startswith(prefix):
            return provider
    return None


@dataclass(frozen=True, slots=True)
class ModelId:
    """
    Immutable model ID with parsed components.

    Invariants:
        - routing_key includes context (different contexts = different models)
        - normalized is consistent for dict keys (strips -hybrid, keeps -cpu)
        - Two ModelIds with same routing_key represent the same loadable variant
        - Cloud IDs skip local -cpu/-hybrid/context parsing.

    Usage:
        model = ModelId.parse("model-8192-hybrid")
        model.base_id        # "model" (for catalog lookup)
        model.context_length # 8192
        model.routing_key    # "model-8192" (for identity/tracking)
        model.normalized     # "model-8192" (for dict keys)

        cloud = ModelId.parse("anthropic/claude-sonnet-4-20250514")
        cloud.is_cloud       # True
        cloud.provider       # "anthropic"

    For dict keys, always use .normalized:
        supervisors[model.normalized] = supervisor
    """

    original: str
    """Original model ID string as provided."""

    base_id: str
    """Base model name without context or suffixes."""

    context_length: int | None
    """Context length if specified (e.g., 8192), None otherwise."""

    is_cpu: bool
    """True if model ID ends with -cpu suffix."""

    is_hybrid: bool
    """True if model ID ends with -hybrid suffix."""

    backend_type: str | None = None
    """Backend type: None (local), 'federated', 'cloud_api', 'vps'."""

    provider: str | None = None
    """Cloud provider name (e.g., 'anthropic'). None for local models."""

    routing_layer: str | None = None
    """Routing prefix: 'openrouter' when routed via OpenRouter, None for
    direct native providers and local models.

    Parsed from model ID strings like ``openrouter/anthropic/claude-3.5-sonnet``.
    Bare cloud IDs (``anthropic/claude-sonnet-4``, ``xai/grok-4``) have
    ``routing_layer=None`` and route to the native provider directly.
    Not part of model identity — excluded from ``normalized``, ``__eq__``,
    and ``__hash__``.
    """

    @classmethod
    def parse(cls, model_id: str | ModelId) -> ModelId:
        """
        Parse a model ID string into a ModelId object.

        Parse order (outermost to innermost):
        1. Routing prefix (openrouter/)
        2. Cloud branch (contains /) or local: device (-cpu), hybrid (-hybrid),
           context (-NNNN)

        Args:
            model_id: Raw model ID string or ModelId object

        Returns:
            Parsed ModelId object (returns input if already ModelId)

        Raises:
            ValueError: If model_id is empty
        """
        # If already a ModelId, return it
        if isinstance(model_id, cls):
            return model_id

        # Now we know it's a string (type narrowing)
        if not isinstance(model_id, str):
            raise TypeError(f"Expected str or ModelId, got {type(model_id)}")
        model_id_str = model_id
        if not model_id_str or not model_id_str.strip():
            raise ValueError("Model ID cannot be empty")

        original = model_id_str

        # Strip known routing prefix (openrouter/) before parsing.
        routing_layer: str | None = None
        if "/" in model_id_str:
            first_segment = model_id_str.split("/", 1)[0]
            if first_segment in _ROUTING_PREFIXES:
                routing_layer = first_segment
                model_id_str = model_id_str[len(first_segment) + 1 :]

        # Cloud model IDs contain '/' (e.g., 'anthropic/claude-sonnet-4-20250514').
        # Opaque pass-through — no local suffix parsing for cloud IDs.
        if "/" in model_id_str:
            provider_prefix = model_id_str.split("/", 1)[0]
            return cls(
                original=original,
                base_id=model_id_str,
                context_length=None,
                is_cpu=False,
                is_hybrid=False,
                backend_type="cloud_api",
                provider=provider_prefix,
                routing_layer=routing_layer,
            )

        is_cpu = False
        is_hybrid = False
        context_length: int | None = None

        # Step 1: Extract device suffix (-cpu)
        if model_id_str.endswith("-cpu"):
            is_cpu = True
            model_id_str = model_id_str[:-4]

        # Step 2: Extract hybrid suffix (-hybrid)
        if model_id_str.endswith("-hybrid"):
            is_hybrid = True
            model_id_str = model_id_str[:-7]

        # Step 3: Extract context length (-NNNN)
        context_match = _CONTEXT_PATTERN.search(model_id_str)
        if context_match:
            context_length = int(context_match.group(1))
            model_id_str = model_id_str[: context_match.start()]

        return cls(
            original=original,
            base_id=model_id_str,
            context_length=context_length,
            is_cpu=is_cpu,
            is_hybrid=is_hybrid,
        )

    @property
    def routing_key(self) -> str:
        """
        Key for model identity and tracking.

        Invariant: Two models with same routing_key represent the same
        loadable model variant. Different context lengths are different variants.

        Format: "{base_id}[-{context}][-cpu]"
        Note: -hybrid is stripped (hybrid and non-hybrid share routing key)
        Note: Context length IS included (different contexts = different resources)
        """
        return self.normalized  # Delegates to normalized property

    @property
    def normalized(self) -> str:
        """
        Normalized ID for dict keys and tracking.

        Strips -hybrid suffix (informational only).
        Preserves -cpu suffix (affects resource allocation).

        Format: "{base_id}[-{context}][-cpu]"
        """
        parts = [self.base_id]
        if self.context_length is not None:
            parts.append(str(self.context_length))
        result = "-".join(parts)
        if self.is_cpu:
            result += "-cpu"
        return result

    @property
    def process_key(self) -> str:
        """Key for shared physical-process resources (supervisor, socket, PID).

        Hybrid and non-hybrid variants share the same worker process, so this
        key strips -hybrid (same as ``normalized``). Use for ProcessState
        lookups where the underlying resource is a single OS process.
        """
        return self.normalized

    @property
    def tracking_key(self) -> str:
        """Key for per-variant state tracking (state machine, ModelResourceInfo).

        Preserves -hybrid because each variant has independent lifecycle state:
        a hybrid variant can be BUSY while its non-hybrid sibling is unloading.
        """
        parts = [self.base_id]
        if self.context_length is not None:
            parts.append(str(self.context_length))
        result = "-".join(parts)
        if self.is_hybrid:
            result += "-hybrid"
        if self.is_cpu:
            result += "-cpu"
        return result

    @property
    def catalog_lookup_id(self) -> str:
        """
        ID for catalog lookups (base + context for config resolution).

        Format: "{base_id}-{context}" or "{base_id}"
        """
        if self.context_length is not None:
            return f"{self.base_id}-{self.context_length}"
        return self.base_id

    @property
    def synthetic_id(self) -> str:
        """
        Full synthetic ID including all suffixes.

        Format: "{base_id}-{context}[-hybrid][-cpu]"
        """
        parts = [self.base_id]
        if self.context_length is not None:
            parts.append(str(self.context_length))
        result = "-".join(parts)
        if self.is_hybrid:
            result += "-hybrid"
        if self.is_cpu:
            result += "-cpu"
        return result

    @property
    def api_model_id(self) -> str:
        """Model ID to send to the upstream API.

        For bare cloud models (direct native provider), strips the provider
        prefix so the upstream receives just the model name (e.g.
        ``claude-sonnet-4``).  For ``openrouter/`` routing, returns
        ``base_id`` as-is (OpenRouter expects ``provider/model``). Local
        models also return ``base_id`` unchanged.

        Invariant: ∀ result: ¬endswith("-mcp") — upstream APIs never accept
        this Stargate-level annotation.
        """
        if self.routing_layer == "openrouter":
            return self.base_id
        if self.provider:
            prefix = f"{self.provider}/"
            if self.base_id.startswith(prefix):
                result = self.base_id[len(prefix) :]
                if result.endswith("-mcp"):
                    result = result[:-4]
                return result
        base = self.base_id
        if base.endswith("-mcp"):
            base = base[:-4]
        return base

    @property
    def is_synthetic(self) -> bool:
        """True if this is a synthetic ID with context length."""
        return self.context_length is not None

    @property
    def is_cloud(self) -> bool:
        """True if this model is served by a cloud API provider."""
        return self.backend_type == "cloud_api"

    @property
    def entity_id(self) -> str:
        """Cortex model entity id (``model:<slug>``) for this identifier.

        Convenience accessor delegating to
        ``model_id.entity.canonical_model_entity_id``. Use this when a
        caller has a ``ModelId`` in hand and needs the Cortex id without
        knowing the free-function exists.
        """
        # Lazy import: entity.py imports ModelId, so the symmetric import
        # at module top-level would be circular.
        from .entity import canonical_model_entity_id

        return canonical_model_entity_id(self)

    def matches(self, other: ModelId | str) -> bool:
        """
        Check if two model identifiers resolve to the same normalized key
        and backend_type.

        Examples:
            ModelId.parse("model-8192").matches("model-8192-hybrid")  # True
            ModelId.parse("model-8192").matches("model-8192-cpu")     # False
        """
        if isinstance(other, str):
            other = ModelId.parse(other)
        return (
            self.normalized == other.normalized
            and self.backend_type == other.backend_type
        )

    def __eq__(self, other: object) -> bool:
        """
        Equality comparison using normalized IDs and backend_type.

        Supports comparison with strings (parses and normalizes):
            ModelId.parse("model-8192") == "model-8192-hybrid"  # True
            ModelId.parse("model-8192") == "model-8192-cpu"     # False

        ModelId to ModelId comparison uses (normalized, backend_type):
            ModelId.parse("model-8192") == ModelId.parse("model-8192-hybrid")  # True

        Returns:
            True if normalized IDs and backend_type match, False otherwise.
            Returns NotImplemented for non-ModelId, non-string types.
        """
        if isinstance(other, str):
            try:
                other = ModelId.parse(other)
            except ValueError:
                return False
        if not isinstance(other, ModelId):
            return NotImplemented
        return (
            self.normalized == other.normalized
            and self.backend_type == other.backend_type
        )

    def __hash__(self) -> int:
        """
        Hash based on normalized ID for use in sets/dicts.

        Invariant: hash(a) == hash(b) whenever a == b (required by Python).
        Since ModelId.__eq__ supports string comparisons by normalizing both
        sides, __hash__ must use only normalized so that:
            hash(ModelId.parse('x')) == hash('x')  when both normalize to 'x'

        Cloud model IDs (e.g., 'anthropic/claude-sonnet-4-20250514') always
        have different normalized forms than local IDs, so backend_type is not
        needed for hash-level distinction.
        """
        return hash(self.normalized)

    def with_context(self, context: int) -> ModelId:
        """Create a new ModelId with specified context length."""
        return ModelId(
            original=self.original,
            base_id=self.base_id,
            context_length=context,
            is_cpu=self.is_cpu,
            is_hybrid=self.is_hybrid,
            backend_type=self.backend_type,
            provider=self.provider,
            routing_layer=self.routing_layer,
        )

    def with_suffix(self, *, cpu: bool = False, hybrid: bool = False) -> ModelId:
        """Create a new ModelId with specified suffixes."""
        if cpu and hybrid:
            raise ValueError("Cannot have both -cpu and -hybrid suffixes")
        return ModelId(
            original=self.original,
            base_id=self.base_id,
            context_length=self.context_length,
            is_cpu=cpu,
            is_hybrid=hybrid,
            backend_type=self.backend_type,
            provider=self.provider,
            routing_layer=self.routing_layer,
        )

    def __str__(self) -> str:
        """
        String representation — returns original name INCLUDING -hybrid/-cpu.

        WARNING: Do NOT use str(model_id) for comparisons or dict lookups.
        The -hybrid suffix is a gateway-local deployment property (GPU offload),
        not part of model identity. Gateways report loaded models WITHOUT -hybrid,
        so str() comparisons WILL FAIL for hybrid models.

        Use instead:
            - model_id.routing_key   → identity/tracking (strips -hybrid)
            - model_id.normalized    → dict keys (strips -hybrid)
            - model_id == other      → comparison (normalizes automatically)
            - model_id.synthetic_id  → wire serialization (preserves all flags)
            - f"{model_id!r}"        → logging with full component visibility
        """
        return self.original

    def __repr__(self) -> str:
        """Detailed representation showing components."""
        parts = [f"ModelId('{self.original}'"]
        if self.context_length:
            parts.append(f"context={self.context_length}")
        if self.is_cpu:
            parts.append("cpu=True")
        if self.is_hybrid:
            parts.append("hybrid=True")
        if self.backend_type:
            parts.append(f"backend={self.backend_type}")
        if self.provider:
            parts.append(f"provider={self.provider}")
        if self.routing_layer:
            parts.append(f"routing={self.routing_layer}")
        return ", ".join(parts) + ")"

    # String-like behavior for compatibility
    def __len__(self) -> int:
        return len(self.original)

    def __contains__(self, item: str) -> bool:
        return item in self.original

    def startswith(self, prefix: str) -> bool:
        return self.original.startswith(prefix)

    def endswith(self, suffix: str) -> bool:
        return self.original.endswith(suffix)


def parse_model_id(model_id: str) -> ModelId:
    """Convenience function to parse a model ID string."""
    return ModelId.parse(model_id)


def get_compute_type(model_id: ModelId | str) -> str:
    """
    Classify model by compute type based on suffix.

    Args:
        model_id: ModelId object or string to parse

    Returns:
        "cpu" | "hybrid" | "gpu"
    """
    if isinstance(model_id, str):
        model_id = ModelId.parse(model_id)

    if model_id.is_cpu:
        return "cpu"
    elif model_id.is_hybrid:
        return "hybrid"
    else:
        return "gpu"
