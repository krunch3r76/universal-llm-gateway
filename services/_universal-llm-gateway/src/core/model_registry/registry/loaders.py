"""Profile loader selection and merged loader configuration for model workers."""

from typing import Any

from universal_logging import get_logger

from .identifiers import normalize_model_id

logger = get_logger(__name__)


class LoaderMixin:
    """Select profile loaders and compile base_loader with context-specific overrides."""

    @staticmethod
    def _select_profile_loader(
        profiles: dict[str, Any],
        requested_ctx: int | None,
        is_cpu: bool = False,
    ) -> dict[str, Any] | None:
        """
        Select appropriate profile loader configuration.

        For GGUF profiles that lack an explicit context-length key, injects
        ``n_ctx`` from the numeric profile key (e.g. ``'65536'`` → ``n_ctx=65536``).
        Skipped when ``max_model_len`` is already present (vLLM models).

        Args:
            profiles: Dictionary of profile configurations
            requested_ctx: Requested context length (None = use highest valid)
            is_cpu: Whether selecting from CPU profiles

        Returns:
            Profile loader config dict, or None if requested_ctx has no exact match.
            None signals a hard reject — callers must not attempt to load the model.
        """
        if not profiles:
            return {}

        selected_key: str | None = None
        loader_config: dict[str, Any] = {}

        if requested_ctx is None:
            # Use the profile with the highest integer key that has valid resources
            profile_keys = [key for key in profiles.keys() if key.isdigit()]
            if profile_keys:
                # Filter for profiles with non-null ram_mb and vram_mb
                valid_keys = []
                for key in profile_keys:
                    profile = profiles[key]
                    resources = profile.get("resources", {})
                    if is_cpu:
                        # For CPU profiles, vram_mb should be 0
                        if resources.get("vram_mb") == 0:
                            valid_keys.append(key)
                    else:
                        # For GPU profiles, both should be non-null
                        if (
                            resources.get("ram_mb") is not None
                            and resources.get("vram_mb") is not None
                        ):
                            valid_keys.append(key)

                # Use highest key among valid profiles, or fall back to highest
                selected_key = (
                    max(valid_keys, key=int)
                    if valid_keys
                    else max(profile_keys, key=int)
                )
                loader_config = profiles[selected_key].get("loader", {}).copy()
            else:
                # No numeric keys - use first profile (named profiles like "default")
                first_profile = list(profiles.values())[0]
                loader_config = first_profile.get("loader", {}).copy()
        else:
            # Find the best matching context length
            ctx_key = str(requested_ctx)
            if ctx_key in profiles:
                selected_key = ctx_key
                loader_config = profiles[ctx_key].get("loader", {}).copy()
            else:
                # ∀ synthetic model ID with context suffix: exact profile key required.
                # Stargate owns context selection; Gateway must not silently substitute.
                numeric_keys = sorted(int(k) for k in profiles.keys() if k.isdigit())
                logger.error(
                    f"[registry] Context {requested_ctx} not found in profiles. "
                    f"Available: {numeric_keys}. "
                    f"Re-run model measurement to add this context profile."
                )
                return None

        # Profile key is the authoritative context length for llama-cpp GGUF profiles.
        # ∀ numeric profile key k: loader["n_ctx"] must equal int(k).
        # An explicit n_ctx in the loader (from a stale measurement pass) must not
        # override the profile key — the synthetic model ID encodes the intended context.
        # vLLM models use max_model_len instead; skip for those.
        if selected_key and selected_key.isdigit():
            ctx_value = int(selected_key)
            if "max_model_len" not in loader_config:
                stale_value = loader_config.get("n_ctx")
                if stale_value is not None and stale_value != ctx_value:
                    logger.error(
                        f"[registry] Stale n_ctx={stale_value} in profile '{selected_key}' "
                        f"loader overridden to ctx_value={ctx_value}. "
                        f"Re-run model measurement on this edge node to correct the catalog."
                    )
                loader_config["n_ctx"] = ctx_value

        logger.info(
            f"[registry] _select_profile_loader: selected_key={selected_key}, "
            f"profile_loader keys={list(loader_config.keys())}"
        )
        return loader_config

    def get_model_loader_config(
        self, model_id: str, requested_ctx: int | None = None
    ) -> dict[str, Any] | None:
        """
        Get model loader configuration compiled from base_loader and specific profile.

        Normalizes model_id for catalog lookup (strips -hybrid suffix).
        Accepts synthetic model IDs (e.g., 'model-name-32768-cpu') and extracts
        context length and CPU/GPU specification from the ID.
        """
        # Normalize for catalog lookup
        canonical_id = normalize_model_id(model_id)

        # Try to resolve as synthetic ID
        synthetic_info = self._resolve_synthetic_id_info(canonical_id)
        if synthetic_info:
            base_model_id, context_length, is_cpu, is_hybrid = synthetic_info
            # Use the context length from synthetic ID, override requested_ctx
            requested_ctx = context_length
        else:
            # Not a synthetic ID, use requested_ctx as-is
            is_cpu = False

        model_config = self.get_model_config(canonical_id)
        if not model_config:
            return None

        # For simple loader format
        if "loader" in model_config:
            return model_config.get("loader", {})

        # For profile-based format
        base_loader = model_config.get("base_loader", {})

        # Determine which profile type to use
        if synthetic_info and is_cpu:
            # Use cpu_profiles for CPU synthetic IDs
            profiles = model_config.get("cpu_profiles", {})
        else:
            # Use regular profiles for GPU or base model IDs
            profiles = model_config.get("profiles", {})

        # Select profile-specific loader overrides if profiles exist.
        # Returns None when requested_ctx has no exact profile match — hard reject.
        profile_loader = self._select_profile_loader(profiles, requested_ctx, is_cpu)
        if profile_loader is None:
            return None

        # Merge base_loader with profile-specific overrides
        # base_loader contains shared config (including vision params)
        # profile_loader contains context-specific overrides (n_ctx, n_gpu_layers)
        merged = {**base_loader, **profile_loader}
        logger.info(
            f"[registry] Merged loader config: base keys={list(base_loader.keys())}, "
            f"profile keys={list(profile_loader.keys())}, "
            f"merged keys={list(merged.keys())}"
        )
        return merged
