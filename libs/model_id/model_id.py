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


@dataclass(frozen=True, slots=True)
class ModelId:
    """
    Immutable model ID with parsed components.

    Invariants:
        - routing_key includes context (different contexts = different models)
        - normalized is consistent for dict keys (strips -hybrid, keeps -cpu)
        - Two ModelIds with same routing_key represent the same loadable variant

    Usage:
        model = ModelId.parse("model-8192-hybrid")
        model.base_id        # "model" (for catalog lookup)
        model.context_length # 8192
        model.routing_key    # "model-8192" (for identity/tracking)
        model.normalized     # "model-8192" (for dict keys)

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

    @classmethod
    def parse(cls, model_id: str | ModelId) -> ModelId:
        """
        Parse a model ID string into a ModelId object.

        Parse order (outermost to innermost):
        1. Device suffix (-cpu)
        2. Hybrid suffix (-hybrid)
        3. Context length (-NNNN)

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
    def is_synthetic(self) -> bool:
        """True if this is a synthetic ID with context length."""
        return self.context_length is not None

    def matches(self, other: ModelId | str) -> bool:
        """
        Check if two model identifiers resolve to the same normalized key.

        Examples:
            ModelId.parse("model-8192").matches("model-8192-hybrid")  # True
            ModelId.parse("model-8192").matches("model-8192-cpu")     # False
        """
        if isinstance(other, str):
            other = ModelId.parse(other)
        return self.normalized == other.normalized

    def __eq__(self, other: object) -> bool:
        """
        Equality comparison using normalized IDs.

        Supports comparison with strings (parses and normalizes):
            ModelId.parse("model-8192") == "model-8192-hybrid"  # True
            ModelId.parse("model-8192") == "model-8192-cpu"     # False

        ModelId to ModelId comparison uses normalized:
            ModelId.parse("model-8192") == ModelId.parse("model-8192-hybrid")  # True

        Returns:
            True if normalized IDs match, False otherwise.
            Returns NotImplemented for non-ModelId, non-string types.
        """
        if isinstance(other, str):
            try:
                other = ModelId.parse(other)
            except ValueError:
                return False
        if not isinstance(other, ModelId):
            return NotImplemented
        return self.normalized == other.normalized

    def __hash__(self) -> int:
        """
        Hash based on normalized ID for use in sets/dicts.

        Invariant: Two ModelIds with same normalized ID have same hash.
        This allows ModelIds to be used as dictionary keys and set elements.

        Examples:
            hash(ModelId.parse("model-8192")) == hash(ModelId.parse("model-8192-hybrid"))  # True
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
