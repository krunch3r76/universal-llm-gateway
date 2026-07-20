"""VRAM and RAM resource requirements plus token limit queries from profiles."""


class ResourcesMixin:
    """Query profile resources, max tokens, and derived limits for model IDs."""

    def get_model_resources(self, model_id: str) -> dict[str, int] | None:
        """
        Get model resource requirements (RAM and VRAM) from profile configuration.

        For synthetic model IDs (e.g., 'model-name-131072'), extracts resources from
        the specific profile. Returns None if profile or resources not found.

        Args:
            model_id: Model ID (can be synthetic with context length)

        Returns:
            Dict with 'ram_mb' and 'vram_mb' keys, or None if not found
        """
        # Try to resolve as synthetic ID to get context length
        synthetic_info = self._resolve_synthetic_id_info(model_id)
        if synthetic_info:
            base_model_id, context_length, is_cpu, is_hybrid = synthetic_info
            requested_ctx = context_length
        else:
            # Not a synthetic ID, no specific context to look up
            requested_ctx = None
            is_cpu = False

        model_config = self.get_model_config(model_id)
        if not model_config or not isinstance(model_config, dict):
            return None

        # Determine profile type
        profile_type = "cpu_profiles" if is_cpu else "profiles"
        profiles = model_config.get(profile_type, {})

        if not profiles:
            return None

        # If we have a specific context from synthetic ID, look it up
        if requested_ctx is not None:
            ctx_key = str(requested_ctx)
            profile = profiles.get(ctx_key)
            if profile:
                resources = profile.get("resources", {})
                ram_mb = resources.get("ram_mb")
                vram_mb = resources.get("vram_mb")
                if ram_mb is not None and vram_mb is not None:
                    return {"ram_mb": ram_mb, "vram_mb": vram_mb}
        else:
            # No specific context - use profile with highest context that has resources
            profile_keys = [key for key in profiles.keys() if key.isdigit()]
            if profile_keys:
                # Filter for profiles with non-null resources
                valid_keys = []
                for key in profile_keys:
                    profile = profiles[key]
                    resources = profile.get("resources", {})
                    if is_cpu:
                        # For CPU profiles, vram_mb should be 0
                        if (
                            resources.get("vram_mb") == 0
                            and resources.get("ram_mb") is not None
                        ):
                            valid_keys.append(key)
                    else:
                        # For GPU profiles, both should be non-null
                        if (
                            resources.get("ram_mb") is not None
                            and resources.get("vram_mb") is not None
                        ):
                            valid_keys.append(key)

                # Use highest key among valid profiles
                if valid_keys:
                    selected_key = max(valid_keys, key=int)
                    profile = profiles[selected_key]
                    resources = profile.get("resources", {})
                    return {
                        "ram_mb": resources.get("ram_mb"),
                        "vram_mb": resources.get("vram_mb"),
                    }

            # Named-profile models (for example cross-encoder/default) do not
            # expose numeric context keys. Use the first profile that has valid
            # resource fields so INIT/GATEWAY_SNAPSHOT can advertise them.
            for profile in profiles.values():
                resources = profile.get("resources", {})
                ram_mb = resources.get("ram_mb")
                vram_mb = resources.get("vram_mb")
                if ram_mb is None or vram_mb is None:
                    continue
                if is_cpu and vram_mb != 0:
                    continue
                return {"ram_mb": ram_mb, "vram_mb": vram_mb}

        return None

    def get_model_max_tokens(
        self, model_id: str, requested_ctx: int | None = None
    ) -> int | None:
        """Get maximum tokens for a model from active loader configuration"""
        model_config_key = self.find_config_key_for_openai_id(model_id)
        if not model_config_key:
            return None

        models_data = self.model_loaders_config.get("models", {})
        model_config = models_data.get(model_config_key, {})

        if not isinstance(model_config, dict):
            return None

        # For simple loader config format
        if "loader" in model_config:
            loader_config = model_config.get("loader", {})
            return loader_config.get("max_model_len") or loader_config.get("n_ctx")

        # Check if this is a synthetic CPU ID to determine which profiles to use
        synthetic_info = self._resolve_synthetic_id_info(model_id)
        is_cpu = synthetic_info[2] if synthetic_info else False
        # is_hybrid uses GPU profiles, so we only need is_cpu here

        # Determine which profile type to check
        if is_cpu:
            # For CPU synthetic IDs, check cpu_profiles first
            profiles = model_config.get("cpu_profiles", {})
            if not profiles:
                # Fallback to regular profiles if cpu_profiles not found
                profiles = model_config.get("profiles", {})
        else:
            # For GPU or base model IDs, check profiles first
            profiles = model_config.get("profiles", {})
            if not profiles:
                # Fallback to cpu_profiles if profiles not found
                profiles = model_config.get("cpu_profiles", {})

        # For profile-based format
        if profiles:
            # Use context length from synthetic ID if available,
            # otherwise use requested_ctx
            effective_ctx = synthetic_info[1] if synthetic_info else requested_ctx

            if effective_ctx is None:
                # Use the profile with the highest integer key that
                # has non-null resource values
                profile_keys = [key for key in profiles.keys() if key.isdigit()]
                if profile_keys:
                    # Filter for profiles with non-null ram_mb and vram_mb
                    valid_keys = []
                    for key in profile_keys:
                        profile = profiles[key]
                        resources = profile.get("resources", {})
                        if (
                            resources.get("ram_mb") is not None
                            and resources.get("vram_mb") is not None
                        ):
                            valid_keys.append(key)

                    # Use highest key among valid profiles, or fall back to highest key
                    selected_key = (
                        max(valid_keys, key=int)
                        if valid_keys
                        else max(profile_keys, key=int)
                    )
                    profile = profiles[selected_key]
                    loader_config = profile.get("loader", {})
                    return loader_config.get("max_model_len") or loader_config.get(
                        "n_ctx"
                    )

                # If no numeric keys found, use the first profile
                first_profile = list(profiles.values())[0]
                loader_config = first_profile.get("loader", {})
                return loader_config.get("max_model_len") or loader_config.get("n_ctx")

            # Exact match required — no silent substitution.
            ctx_key = str(effective_ctx)
            if ctx_key in profiles:
                loader_config = profiles[ctx_key].get("loader", {})
                return loader_config.get("max_model_len") or loader_config.get("n_ctx")
            else:
                return None

        # Legacy fallback - try metadata (will be removed)
        info = model_config.get("info", {})
        context_length = info.get("training_context_length")
        if context_length:
            return context_length

        # Also check metadata for backward compatibility
        metadata = model_config.get("metadata", {})
        context_length = metadata.get("training_context_length")
        if context_length:
            return context_length

        return model_config.get("context_length")

    def get_model_limits(
        self, model_id: str, requested_ctx: int | None = None
    ) -> dict[str, int] | None:
        """Get model limits from active loader configuration"""
        context_length = self.get_model_max_tokens(model_id, requested_ctx)

        if not context_length:
            return None

        return {"max_tokens": context_length, "max_input_tokens": context_length}
