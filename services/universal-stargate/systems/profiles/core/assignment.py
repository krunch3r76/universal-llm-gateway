"""Profile assignment - manages global/model profile state."""

from typing import Any

from universal_logging import get_logger

from utils.model_basename import get_model_profile

from ..config.loader import ProfileConfigLoader

logger = get_logger(__name__)


class ProfileAssignment:
    """
    Manage profile assignments (global and per-model).

    All assignment methods validate profile existence and fail-fast on unknown profiles.
    """

    def __init__(self, config: ProfileConfigLoader) -> None:
        self._config = config
        self._global_profile_name: str | None = None
        self._model_profile_names: dict[str, str] = {}

    # Getters

    def get_global(self) -> str | None:
        """Get global profile name."""
        return self._global_profile_name

    def get_model(self, model_id: str) -> str | None:
        """Get profile for specific model."""
        return self._model_profile_names.get(model_id)

    def get_active_profiles(self) -> dict[str, Any]:
        """Get all active profile assignments."""
        return {
            "global": self._global_profile_name,
            "model_specific": self._model_profile_names.copy(),
        }

    def get_profiles_to_apply(self, model_id: str) -> list[str]:
        """Get ordered list of profiles to apply [global, model]."""
        profiles: list[str] = []
        if self._global_profile_name:
            profiles.append(self._global_profile_name)
        model_profile = self._model_profile_names.get(model_id)
        if model_profile:
            profiles.append(model_profile)
        return profiles

    # Setters (fail-fast on unknown)

    def set_global(self, profile_name: str) -> dict[str, Any]:
        """Set global profile. Raises ValueError if unknown."""
        if not self._config.exists(profile_name):
            raise ValueError(f"Unknown profile: {profile_name}")

        self._global_profile_name = profile_name
        profile = self._config.get(profile_name)
        return {
            "name": profile_name,
            "description": profile.get("description", "") if profile else "",
        }

    def clear_global(self) -> dict[str, Any]:
        """Clear global profile."""
        self._global_profile_name = None
        return {"cleared": "global"}

    def set_model(self, model_id: str, profile_name: str) -> dict[str, Any]:
        """Set profile for model. Raises ValueError if unknown."""
        if not self._config.exists(profile_name):
            raise ValueError(f"Unknown profile: {profile_name}")

        self._model_profile_names[model_id] = profile_name
        profile = self._config.get(profile_name)
        return {
            "model_id": model_id,
            "name": profile_name,
            "description": profile.get("description", "") if profile else "",
        }

    def clear_model(self, model_id: str) -> dict[str, Any]:
        """Clear profile for model."""
        self._model_profile_names.pop(model_id, None)
        return {"cleared": "model_specific", "model_id": model_id}

    # Auto-assignment

    def auto_assign_by_basename(self, model_id: str) -> str | None:
        """Auto-assign profile using basename matching."""
        try:
            profile_config = get_model_profile(model_id)

            if isinstance(profile_config, dict):
                logger.debug(
                    f"Model '{model_id}' has structured config, "
                    "skipping text profile auto-assignment"
                )
                return None

            if profile_config and self._config.exists(profile_config):
                current = self.get_model(model_id)
                if not current:
                    self._model_profile_names[model_id] = profile_config
                    logger.info(
                        f"Auto-assigned profile '{profile_config}' to '{model_id}'"
                    )
                    return profile_config
                return current

            return None
        except Exception as e:
            logger.error(f"Error in auto-assignment for '{model_id}': {e}")
            return None

    def ensure_assigned(self, model_id: str) -> str | None:
        """Ensure model has profile, using auto-assignment if needed."""
        current = self.get_model(model_id)
        if not current:
            current = self.auto_assign_by_basename(model_id)
        return current
