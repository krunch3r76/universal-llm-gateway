"""
AudioProfileManager: VAD and Whisper quality profiles for audio streaming.

Loads profiles from config/audio_profiles.yaml at startup. Hot-reload is future work;
any reload must occur outside request path (startup or background watcher).
Provides profile resolution with precedence: explicit params > profile > defaults.
"""

from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

# Import local config dataclasses (no inference_djinn dependency needed for proxy)
# Note: Imports moved to method level to avoid circular dependencies

logger = get_logger(__name__)

# Hardcoded fallback defaults (used when YAML fails to load)
FALLBACK_VAD_PROFILE = "streaming-optimized"
FALLBACK_WHISPER_PROFILE = "quality"


class AudioProfileManager:
    """Manage VAD and Whisper quality profiles for audio streaming."""

    def __init__(self, config_path: str | None = None) -> None:
        if config_path is None:
            config_path = str(
                Path(__file__).parents[3] / "config" / "audio_profiles.yaml"
            )
        self._config_path = Path(config_path)
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        """Load profiles from YAML configuration."""
        try:
            with open(self._config_path) as f:
                config = yaml.safe_load(f)
            logger.info(f"Loaded audio profiles from {self._config_path}")
            config = config or {}
            self._validate_defaults(config)
            self._validate_profile_shapes(config)
            return config
        except Exception as e:
            logger.error(f"Failed to load audio profiles: {e}")
            return {"defaults": {}, "vad_profiles": {}, "whisper_profiles": {}}

    def _validate_defaults(self, config: dict[str, Any]) -> None:
        """Warn if defaults reference missing profiles."""
        defaults = config.get("defaults", {})
        vad_profiles = config.get("vad_profiles", {})
        whisper_profiles = config.get("whisper_profiles", {})
        vad_name = defaults.get("vad_profile")
        if vad_name and vad_name not in vad_profiles:
            logger.warning("Default VAD profile '%s' missing in vad_profiles", vad_name)
        whisper_name = defaults.get("whisper_profile")
        if whisper_name and whisper_name not in whisper_profiles:
            logger.warning(
                "Default Whisper profile '%s' missing in whisper_profiles", whisper_name
            )

    def _validate_profile_shapes(self, config: dict[str, Any]) -> None:
        """Warn if profiles are not dicts (guard against malformed YAML)."""
        for name, data in (config.get("vad_profiles") or {}).items():
            if not isinstance(data, dict):
                logger.warning(
                    "VAD profile '%s' must be a mapping, got %s",
                    name,
                    type(data).__name__,
                )
        for name, data in (config.get("whisper_profiles") or {}).items():
            if not isinstance(data, dict):
                logger.warning(
                    "Whisper profile '%s' must be a mapping, got %s",
                    name,
                    type(data).__name__,
                )

    @property
    def default_vad_profile(self) -> str:
        """Get default VAD profile name."""
        return self._config.get("defaults", {}).get("vad_profile", FALLBACK_VAD_PROFILE)

    @property
    def default_whisper_profile(self) -> str:
        """Get default Whisper profile name."""
        return self._config.get("defaults", {}).get(
            "whisper_profile", FALLBACK_WHISPER_PROFILE
        )

    def get_vad_profiles(self) -> dict[str, dict[str, Any]]:
        """Get all VAD profile definitions."""
        return self._config.get("vad_profiles", {})

    def get_whisper_profiles(self) -> dict[str, dict[str, Any]]:
        """Get all Whisper profile definitions."""
        return self._config.get("whisper_profiles", {})

    def get_vad_profile(self, name: str) -> dict[str, Any] | None:
        """Get a specific VAD profile by name."""
        return self.get_vad_profiles().get(name)

    def get_whisper_profile(self, name: str) -> dict[str, Any] | None:
        """Get a specific Whisper profile by name."""
        return self.get_whisper_profiles().get(name)

    def resolve_vad_profile_name(
        self, explicit_profile: str | None, model_default: str | None = None
    ) -> str:
        """
        Resolve VAD profile name using precedence rules.

        Precedence: explicit_profile > model_default > global default > fallback
        """
        vad_profiles = self.get_vad_profiles()

        # 1. Explicit profile from request
        if explicit_profile:
            if explicit_profile in vad_profiles:
                return explicit_profile
            logger.warning(
                "VAD profile '%s' not found, trying model default", explicit_profile
            )

        # 2. Model-based default
        if model_default and model_default in vad_profiles:
            return model_default

        # 3. Global default from config
        global_default = self.default_vad_profile
        if global_default in vad_profiles:
            return global_default

        # 4. Hardcoded fallback
        logger.warning(
            "Using hardcoded fallback VAD profile '%s'", FALLBACK_VAD_PROFILE
        )
        return FALLBACK_VAD_PROFILE

    def resolve_whisper_profile_name(
        self, explicit_profile: str | None, model_default: str | None = None
    ) -> str:
        """
        Resolve Whisper profile name using precedence rules.

        Precedence: explicit_profile > model_default > global default > fallback
        """
        whisper_profiles = self.get_whisper_profiles()

        # 1. Explicit profile from request
        if explicit_profile:
            if explicit_profile in whisper_profiles:
                return explicit_profile
            logger.warning(
                "Whisper profile '%s' not found, trying model default", explicit_profile
            )

        # 2. Model-based default
        if model_default and model_default in whisper_profiles:
            return model_default

        # 3. Global default from config
        global_default = self.default_whisper_profile
        if global_default in whisper_profiles:
            return global_default

        # 4. Hardcoded fallback
        logger.warning(
            "Using hardcoded fallback Whisper profile '%s'", FALLBACK_WHISPER_PROFILE
        )
        return FALLBACK_WHISPER_PROFILE

    def apply_vad_profile(
        self,
        profile: str | None,
        model_default: str | None = None,
        **explicit_params: Any,
    ) -> dict[str, Any]:
        """
        Apply VAD profile with explicit parameter overrides.

        Args:
            profile: Profile name (None = use precedence resolution)
            model_default: Model-based default profile name
            **explicit_params: Explicit parameter overrides (only non-None applied)

        Returns:
            Effective parameters dict
        """
        profile_name = self.resolve_vad_profile_name(profile, model_default)
        profile_data = self.get_vad_profile(profile_name) or {}

        logger.debug("Resolved VAD profile '%s' for request", profile_name)

        # Start with profile defaults (exclude description)
        effective = {k: v for k, v in profile_data.items() if k != "description"}

        # Override with explicit non-None params
        for key, value in explicit_params.items():
            if value is not None:
                effective[key] = value

        return effective

    def apply_whisper_profile(
        self,
        profile: str | None,
        model_default: str | None = None,
        beam_size: int | None = None,
        temperature: str | None = None,
        condition_on_previous_text: bool | None = None,
    ) -> dict[str, Any]:
        """
        Apply Whisper quality profile with explicit parameter overrides.

        Args:
            profile: Profile name (None = use precedence resolution)
            model_default: Model-based default profile name
            beam_size: Override beam size
            temperature: Override temperature (comma-separated string)
            condition_on_previous_text: Override context flag

        Returns:
            Effective parameters dict with normalized types
        """
        profile_name = self.resolve_whisper_profile_name(profile, model_default)
        profile_data = self.get_whisper_profile(profile_name) or {}

        logger.debug("Resolved Whisper profile '%s' for request", profile_name)

        # Start with profile defaults
        effective: dict[str, Any] = {}

        # Beam size (clamp to valid range)
        beam = beam_size if beam_size is not None else profile_data.get("beam_size", 5)
        effective["beam_size"] = max(1, min(20, int(beam)))

        # Temperature (parse comma-separated to list)
        temp_str = (
            temperature
            if temperature is not None
            else profile_data.get("temperature", "0.0")
        )
        effective["temperature"] = self._parse_temperature(str(temp_str))

        # Context flag
        ctx = (
            condition_on_previous_text
            if condition_on_previous_text is not None
            else profile_data.get("condition_on_previous_text", True)
        )
        effective["condition_on_previous_text"] = bool(ctx)

        return effective

    def _parse_temperature(self, temp_str: str) -> list[float]:
        """Parse comma-separated temperature string to validated float list."""
        try:
            stripped = temp_str.strip()
            if not stripped:
                return [0.0]
            temps = [float(t.strip()) for t in stripped.split(",")]
            # Validate range [0.0, 2.0] and clamp length to 10 entries
            return [max(0.0, min(2.0, t)) for t in temps][:10]
        except ValueError:
            return [0.0]

    @property
    def defaults(self) -> dict[str, Any]:
        """Get defaults configuration section."""
        return self._config.get("defaults", {})

    @property
    def default_min_word_probability(self) -> float:
        """Get default min_word_probability."""
        return self._config.get("defaults", {}).get("min_word_probability", 0.15)

    def get_overlap_correction_config(
        self,
        profile: str | None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """
        Get overlap correction config from profile with optional override.

        Resolution precedence:
        1. Explicit `enabled` param (if provided)
        2. VAD profile's overlap_correction settings
        3. Global defaults overlap_correction settings

        Args:
            profile: VAD profile name (resolved profile)
            enabled: Explicit override for enabled flag (None = use profile/default)

        Returns:
            Overlap correction config dict with keys: enabled, hold_word_count,
            max_time_gap_ms, min_prefix_ratio
        """
        # Get defaults from config
        default_cfg = self.defaults.get("overlap_correction", {})

        # Get profile-specific settings (may override defaults)
        profile_data = self.get_vad_profile(profile) if profile else {}
        profile_cfg = profile_data.get("overlap_correction", {}) if profile_data else {}

        # Merge: profile overrides defaults
        result = {**default_cfg, **profile_cfg}

        # Explicit param overrides everything
        if enabled is not None:
            result["enabled"] = enabled

        return result

    def get_second_pass_config(
        self,
        profile: str | None = None,
    ):
        """
        Get second-pass config from profile or defaults.

        Resolution precedence:
        1. VAD profile's second_pass settings
        2. Global defaults second_pass settings
        3. Hardcoded fallback defaults

        Args:
            profile: VAD profile name (resolved profile)

        Returns:
            SecondPassConfig dataclass instance
        """
        from .schemas.second_pass_config import SecondPassConfig

        # Hardcoded fallback defaults
        fallback = {
            "enabled": True,
            "min_silence_duration_ms": 600,
            "scan_step_ms": 40,
            "max_search_depth_ms": 6000,
            "leave_behind_ms": 200,
            "vad_method": None,
        }

        # Get defaults from config
        default_cfg = self.defaults.get("second_pass", {})

        # Get profile-specific settings (may override defaults)
        profile_data = self.get_vad_profile(profile) if profile else {}
        profile_cfg = profile_data.get("second_pass", {}) if profile_data else {}

        # Merge: fallback < defaults < profile
        merged = {**fallback, **default_cfg, **profile_cfg}

        return SecondPassConfig(
            enabled=merged["enabled"],
            min_silence_duration_ms=merged["min_silence_duration_ms"],
            scan_step_ms=merged["scan_step_ms"],
            max_search_depth_ms=merged["max_search_depth_ms"],
            leave_behind_ms=merged["leave_behind_ms"],
            vad_method=merged["vad_method"],
        )

    def get_context_carryover_config(
        self,
        profile: str | None = None,
    ):
        """
        Get context carryover config from profile or defaults.

        Resolution precedence:
        1. VAD profile's context_carryover settings
        2. Global defaults context_carryover settings
        3. Hardcoded fallback defaults

        Args:
            profile: VAD profile name (resolved profile)

        Returns:
            ContextCarryoverConfig dataclass instance
        """
        from .schemas.context_carryover_config import ContextCarryoverConfig

        # Hardcoded fallback defaults
        fallback = {
            "enabled": False,
            "duration_s": 1.5,
        }

        # Get defaults from config
        default_cfg = self.defaults.get("context_carryover", {})

        # Get profile-specific settings (may override defaults)
        profile_data = self.get_vad_profile(profile) if profile else {}
        profile_cfg = profile_data.get("context_carryover", {}) if profile_data else {}

        # Merge: fallback < defaults < profile
        merged = {**fallback, **default_cfg, **profile_cfg}

        return ContextCarryoverConfig(
            enabled=merged["enabled"],
            duration_s=merged["duration_s"],
        )

    def get_boundary_preservation_config(
        self,
        profile: str | None = None,
    ) -> dict[str, Any]:
        """
        Get boundary config from profile with mutual exclusivity validation.

        Resolution precedence:
        1. VAD profile's streaming.boundaries settings
        2. Global defaults streaming.boundaries settings
        3. Hardcoded fallback defaults

        Args:
            profile: VAD profile name (resolved profile)

        Returns:
            dict with boundary config keys

        Raises:
            ValueError: If adaptive and leave-behind both enabled
        """
        # Hardcoded fallback defaults
        fallback = {
            "adaptive_preservation_enabled": False,
            "max_window_leave_behind_ms": 0,
            "defer_enabled": True,
            "defer_max_ms": 250,
        }

        # Get defaults from config
        default_streaming = self.defaults.get("streaming", {})
        default_boundaries = (
            default_streaming.get("boundaries", {}) if default_streaming else {}
        )

        # Get profile-specific settings (may override defaults)
        profile_data = self.get_vad_profile(profile) if profile else {}
        profile_streaming = profile_data.get("streaming", {}) if profile_data else {}
        profile_boundaries = (
            profile_streaming.get("boundaries", {}) if profile_streaming else {}
        )

        # Merge: fallback < defaults < profile
        result = {**fallback, **default_boundaries, **profile_boundaries}

        # Track source of each key for logging
        sources: dict[str, str] = {}
        for key in fallback:
            if key in profile_boundaries:
                sources[key] = f"profile:{profile}"
            elif key in default_boundaries:
                sources[key] = "defaults"
            else:
                sources[key] = "fallback"

        # Enforce mutual exclusivity
        adaptive_enabled = result.get("adaptive_preservation_enabled", False)
        leave_behind_ms = result.get("max_window_leave_behind_ms", 0)

        if adaptive_enabled and leave_behind_ms > 0:
            profile_name = profile or "default"
            raise ValueError(
                f"Boundary modes mutually exclusive in '{profile_name}': "
                f"adaptive={adaptive_enabled}, leave_behind={leave_behind_ms}ms"
            )

        # Log resolved values with sources
        logger.info(
            "Boundary config resolved (profile=%s): adaptive=%s [%s], "
            "leave_behind=%dms [%s], defer=%s [%s], defer_max=%dms [%s]",
            profile or "none",
            adaptive_enabled,
            sources.get("adaptive_preservation_enabled", "unknown"),
            leave_behind_ms,
            sources.get("max_window_leave_behind_ms", "unknown"),
            result.get("defer_enabled", True),
            sources.get("defer_enabled", "unknown"),
            result.get("defer_max_ms", 250),
            sources.get("defer_max_ms", "unknown"),
        )

        return result


# Global singleton
_audio_profile_manager: AudioProfileManager | None = None


def get_audio_profile_manager() -> AudioProfileManager:
    """Get global AudioProfileManager instance."""
    global _audio_profile_manager
    if _audio_profile_manager is None:
        _audio_profile_manager = AudioProfileManager()
    return _audio_profile_manager
