"""Model ID normalization, suffix validation, and synthetic ID resolution helpers."""

from typing import Any

from model_id import ModelId
from universal_logging import get_logger

from src.core.synthetic_models import SyntheticModelResolver

logger = get_logger(__name__)


def normalize_model_id(model_id: str) -> str:
    """
    Normalize model ID by stripping informational suffixes.

    The -hybrid suffix is informational only and stripped for operations.
    The -cpu suffix is preserved as it affects resource allocation.

    No instance suffix (`:N`) support - Gateway uses canonical model IDs only.
    Stargate rejects `:N` at the API boundary.

    Args:
        model_id: Model ID to normalize (must be canonical, no `:N`)

    Returns:
        Normalized model ID (e.g., 'model-8192-hybrid' -> 'model-8192')
    """
    return ModelId.parse(model_id).normalized


def _validate_model_id_suffixes(model_id: str) -> str | None:
    """
    Validate model ID does not have conflicting or malformed suffixes.

    Instance suffix (`:N`) is no longer supported - Gateway uses canonical
    model IDs only. Stargate rejects `:N` at the API boundary.

    INTENTIONAL DUPLICATION: This function is duplicated from Stargate's
    `proxy/utils/model_id_utils.py` to avoid cross-service Python dependencies.
    These services are deployed independently and cannot share code imports.

    MAINTENANCE: When updating validation rules, update both:
    - services/universal-stargate/proxy/utils/model_id_utils.py
    - services/_universal-llm-gateway/src/core/model_registry/registry/identifiers.py
    """
    # Reject instance suffix (`:N`) - Gateway uses canonical model IDs only
    if ":" in model_id:
        return "Model ID must not include an instance suffix like ':1' or ':2'"

    cpu_count = model_id.count("-cpu")
    hybrid_count = model_id.count("-hybrid")

    if cpu_count > 1:
        return "Model ID contains duplicated -cpu suffix"
    if hybrid_count > 1:
        return "Model ID contains duplicated -hybrid suffix"

    if cpu_count > 0 and hybrid_count > 0:
        return "Model ID cannot have both -cpu and -hybrid suffixes"

    if cpu_count == 1 and not model_id.endswith("-cpu"):
        return "Invalid -cpu suffix: must be at end of model ID"
    if hybrid_count == 1 and not model_id.endswith("-hybrid"):
        return "Invalid -hybrid suffix: must be at end of model ID"

    return None


class IdentityMixin:
    """Synthetic ID resolution and suffix-validated model ID entrypoint."""

    def _resolve_synthetic_id_info(
        self, model_id: str
    ) -> tuple[str, int, bool, bool] | None:
        """
        Resolve synthetic ID to (base_model_id, context_length, is_cpu, is_hybrid).

        Returns None if not a synthetic ID.
        """
        return SyntheticModelResolver.resolve_synthetic_id(model_id)

    def resolve_model_id(
        self, model_id: str
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        """
        Resolve model ID to config, validating suffixes and normalizing.

        This is the SINGLE ENTRYPOINT for suffix validation in Gateway.
        Returns normalized model ID (strips -hybrid suffix) for consistent
        operations across Gateway and Stargate.

        Args:
            model_id: Model ID to resolve (can be synthetic or base)

        Returns:
            Tuple of (normalized_model_id, config, error_message)
            - If error_message is not None, request should be rejected with 400
            - normalized_model_id is the input model_id after suffix normalization
              (e.g., 'model-8192-hybrid' -> 'model-8192')
            - config is the model configuration dict, or None if not found

        Examples:
            >>> resolve_model_id('model-8192-hybrid')
            ('model-8192', {...config...}, None)
            >>> resolve_model_id('model-8192-cpu')
            ('model-8192-cpu', {...config...}, None)
        """
        error = _validate_model_id_suffixes(model_id)
        if error:
            return model_id, None, error

        # Normalize by stripping -hybrid suffix (informational only)
        canonical_id = normalize_model_id(model_id)

        resolution = SyntheticModelResolver.resolve_synthetic_id(canonical_id)
        if resolution:
            base_model_id, context_length, is_cpu, is_hybrid = resolution
            config = self.get_model_config(base_model_id)
            return canonical_id, config, None

        config = self.get_model_config(canonical_id)
        return canonical_id, config, None
