"""Profile resolution - resolves profile chain and merges parameters."""

from typing import Any

from universal_logging import get_logger

from ..config.loader import ProfileConfigLoader
from ..conversion.engine_mapper import EngineMapper
from ..conversion.parameter_converter import ParameterConverter
from .types import ProfileData

logger = get_logger(__name__)


class ProfileResolver:
    """
    Resolve profile chain and merge parameters for a model.

    Invariant: ∀ k ∈ user_params: profile[k] is never applied
    (profiles fill missing params, never override user-provided values)
    """

    def __init__(
        self,
        config: ProfileConfigLoader,
        engine_mapper: EngineMapper,
        converter: ParameterConverter,
    ) -> None:
        self._config = config
        self._engine_mapper = engine_mapper
        self._converter = converter

    def resolve(
        self,
        model_id: str,
        user_params: dict[str, Any],
        request_profile: str | None,
        model_info: dict[str, Any],
        profiles_to_apply: list[str],
    ) -> ProfileData:
        """
        Resolve profile chain and return merged ProfileData.

        Args:
            model_id: Model identifier
            user_params: User-provided parameters (never overridden)
            request_profile: Profile from request (fail-fast if unknown)
            model_info: Model metadata for engine detection
            profiles_to_apply: Ordered list [global, model]

        Returns:
            ProfileData with merged parameters

        Raises:
            ValueError: If request_profile is unknown (fail-fast)
        """
        actions: list[str] = []
        warnings: list[str] = []

        # Fail-fast on unknown request profile
        if request_profile:
            if not self._config.exists(request_profile):
                raise ValueError(f"Unknown profile requested: {request_profile}")
            profiles_to_apply.append(request_profile)

        # Determine engine
        model_format = (model_info or {}).get("format")
        engine = self._engine_mapper.get_engine(model_format)
        logger.debug(f"Model {model_id} format={model_format} engine={engine}")

        # Merge parameters from profiles
        params: dict[str, Any] = {}
        final_name: str | None = None
        final_system_prompt: str | None = None

        for name in profiles_to_apply:
            profile_def = self._config.get(name)
            if not profile_def:
                continue

            profile_params = self._get_profile_for_engine(
                name=name,
                profile=profile_def,
                engine=engine,
                warnings=warnings,
                model_id=model_id,
            )

            for key, value in profile_params.items():
                if key in user_params:
                    continue  # INVARIANT: Never override user params
                if key not in params:
                    params[key] = value
                    actions.append(f"profile_{name}:{key}={value}")

            system_prompt = profile_def.get("system_prompt")
            if system_prompt:
                final_system_prompt = system_prompt
                final_name = name
                actions.append(
                    f"profile_{name}:system_prompt=<{len(system_prompt)} chars>"
                )

        if profiles_to_apply:
            logger.info(
                f"Resolved profile for {model_id} ({engine}): {profiles_to_apply}"
            )

        return ProfileData(
            name=final_name,
            params=params,
            system_prompt=final_system_prompt,
            actions=actions,
            warnings=warnings,
        )

    def _get_profile_for_engine(
        self,
        name: str,
        profile: dict[str, Any],
        engine: str,
        warnings: list[str],
        model_id: str,
    ) -> dict[str, Any]:
        """Get profile parameters adapted for target engine."""
        result = {}

        metadata_fields = {"description", "system_prompt", "llama_cpp", "vllm"}
        for key, value in profile.items():
            if key not in metadata_fields:
                result[key] = value

        if engine == "llama_cpp":
            result.update(profile.get("llama_cpp", {}))
        elif engine == "vllm":
            vllm_params = profile.get("vllm", {})
            if vllm_params:
                result.update(vllm_params)
            else:
                llama_params = profile.get("llama_cpp", {})
                if llama_params:
                    full_params = result.copy()
                    full_params.update(llama_params)
                    result = self._converter.convert_llama_cpp_to_vllm(full_params)
                    logger.debug(f"Auto-converted llama-cpp to vLLM for '{name}'")
        else:
            warning = (
                f"profile_{name}: unknown engine '{engine}' for model {model_id}; "
                "using shared params only"
            )
            warnings.append(warning)
            logger.warning(warning)

        return result
