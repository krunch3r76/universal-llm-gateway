"""
ProfileManager: Thin orchestrator combining resolution + assignment.

This is the public API for the profiles system. It delegates to:
- ProfileResolver: Profile chain resolution and parameter merging
- ProfileAssignment: Global/model profile state management
"""

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..config.loader import ProfileConfigLoader
from ..conversion.engine_mapper import EngineMapper
from ..conversion.parameter_converter import ParameterConverter
from .assignment import ProfileAssignment
from .resolution import ProfileResolver
from .types import ProfileData

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)


class ProfileManager:
    """
    Manage multi-engine generation parameter profiles.

    Invariants:
    - Config lookups use in-memory data (startup I/O only)
    - User parameters never overridden (fill-only semantics)
    - Unknown profiles raise ValueError (fail-fast)
    """

    def __init__(
        self,
        config_loader: ProfileConfigLoader,
        engine_mapper: EngineMapper | None = None,
    ) -> None:
        self._config = config_loader
        self._engine_mapper = engine_mapper or EngineMapper()
        self._converter = ParameterConverter()

        # Delegates
        self._assignment = ProfileAssignment(config_loader)
        self._resolver = ProfileResolver(
            config_loader, self._engine_mapper, self._converter
        )

    @property
    def profiles_path(self) -> "Path":
        """Get profiles config path (for hot-reload watcher)."""
        return self._config.config_path

    def reload_profiles(self) -> None:
        """Reload profiles from disk (hot-reload support)."""
        self._config.reload()

    # Main resolution API

    def get_complete_profile(
        self,
        model_id: str,
        user_params: dict[str, Any],
        request_profile: str | None,
        model_info: dict[str, Any],
        disable_profile: bool = False,
    ) -> ProfileData:
        """Get complete profile data for a model."""
        if not disable_profile:
            self._assignment.ensure_assigned(model_id)

        profiles_to_apply = self._assignment.get_profiles_to_apply(model_id)
        return self._resolver.resolve(
            model_id, user_params, request_profile, model_info, profiles_to_apply
        )

    # Assignment delegation

    def set_global_profile(self, profile_name: str) -> dict[str, Any]:
        return self._assignment.set_global(profile_name)

    def clear_global_profile(self) -> dict[str, Any]:
        return self._assignment.clear_global()

    def set_model_profile(self, model_id: str, profile_name: str) -> dict[str, Any]:
        return self._assignment.set_model(model_id, profile_name)

    def clear_model_profile(self, model_id: str) -> dict[str, Any]:
        return self._assignment.clear_model(model_id)

    def get_profile(self, model_id: str) -> str | None:
        return self._assignment.get_model(model_id)

    def get_active_profiles(self) -> dict[str, Any]:
        return self._assignment.get_active_profiles()

    def ensure_profile_assigned(self, model_id: str) -> str | None:
        return self._assignment.ensure_assigned(model_id)

    # Introspection

    def get_profile_definitions(self) -> dict[str, Any]:
        result = {}
        for name in self._config.list_profiles():
            profile = self._config.get(name)
            if profile:
                result[name] = {
                    "description": profile.get("description", f"{name} profile"),
                    "system_prompt": profile.get("system_prompt"),
                    "engines_supported": ["llama_cpp", "vllm"],
                }
        return result

    def get_system_prompt(self, profile_name: str) -> str | None:
        profile = self._config.get(profile_name)
        if not profile:
            raise ValueError(f"Unknown profile: {profile_name}")
        return profile.get("system_prompt")

    def is_model_compatible(
        self, model_id: str, model_metadata: Any
    ) -> tuple[bool, str]:
        fmt = model_metadata.format if model_metadata else None
        if not fmt:
            return True, "unknown (will use default engine)"
        engine = self._engine_mapper.get_engine(fmt)
        return True, f"{fmt} ({engine} engine)"

    def get_profile_info(self) -> dict[str, Any]:
        return {
            "profiles_path": str(self._config.config_path),
            "profile_count": len(self._config.list_profiles()),
            "profile_names": self._config.list_profiles(),
            "hot_reload": "supported",
            "global_profile": self._assignment.get_global(),
            "model_profiles": dict(self._assignment._model_profile_names),
        }
