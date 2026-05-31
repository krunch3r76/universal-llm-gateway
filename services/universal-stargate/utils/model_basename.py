"""
Unified Model Basename Matching Utility

This module provides centralized basename matching for model profiles and transformations,
replacing fragmented regex-based approaches with a single, maintainable solution.

Key features:
- Prefix matching with proper boundary detection
- Longest-first matching for specificity (qwen2-5-coder beats qwen2-5)
- Case-insensitive matching for robustness
- Support for hyphenated basenames (deepseek-coder, qwen2-5-coder)
- Configuration loading from YAML files
"""

from pathlib import Path

import yaml
from universal_logging import get_logger

logger = get_logger(__name__)


class UnifiedModelMatcher:
    """Unified basename matching for profiles and transformations"""

    def __init__(self, config_dir: str | None = None):
        """
        Initialize the UnifiedModelMatcher

        Args:
            config_dir: Path to configuration directory (defaults to ../config relative to this file)
        """
        self.basenames: list[str] = []
        self.profile_mappings: dict[str, str | dict] = {}
        self.transformation_mappings: dict[str, dict] = {}

        # Set default config directory if not provided
        if config_dir is None:
            current_file = Path(__file__)
            self.config_dir = current_file.parent.parent / "config"
        else:
            self.config_dir = Path(config_dir)

        self._load_configurations()

    def _load_configurations(self):
        """Load configurations from YAML files"""
        try:
            self._load_profiles()
            self._load_transformations()
            # Sort basenames by length (descending) for longest-first matching
            self.basenames = sorted(set(self.basenames), key=len, reverse=True)
            logger.info(f"Loaded {len(self.basenames)} basenames for matching")
            logger.debug(f"Basenames: {self.basenames}")
        except Exception as e:
            logger.error(f"Failed to load configurations: {e}")
            # Initialize empty collections to prevent crashes
            self.basenames = []
            self.profile_mappings = {}
            self.transformation_mappings = {}

    def _load_profiles(self):
        """Load profile configuration from model_profiles.yaml"""
        profiles_file = self.config_dir / "model_profiles.yaml"

        if not profiles_file.exists():
            logger.warning(f"Profile configuration file not found: {profiles_file}")
            return

        try:
            with open(profiles_file) as f:
                data = yaml.safe_load(f)

            if data and "basename_profiles" in data:
                for basename, profile in data["basename_profiles"].items():
                    basename_lower = basename.lower()
                    self.basenames.append(basename_lower)
                    # Store raw config (str or dict) - no transformation
                    self.profile_mappings[basename_lower] = profile
                    if isinstance(profile, dict):
                        profile_type = "dict"
                    else:
                        profile_type = type(profile).__name__
                    logger.debug(
                        f"Loaded profile mapping: {basename} -> {profile_type}"
                    )

        except Exception as e:
            logger.error(f"Failed to load profiles from {profiles_file}: {e}")

    def _load_transformations(self):
        """Load transformation configuration from model_transformations.yaml"""
        transformations_file = self.config_dir / "model_transformations.yaml"

        if not transformations_file.exists():
            logger.warning(
                f"Transformation configuration file not found: {transformations_file}"
            )
            return

        try:
            with open(transformations_file) as f:
                data = yaml.safe_load(f)

            if data and "transformations" in data:
                for name, config in data["transformations"].items():
                    # Check if transformation is enabled (default to True for backward
                    # compatibility)
                    if not config.get("enabled", True):
                        logger.info(f"Skipping disabled transformation: {name}")
                        continue

                    if "basename" in config:
                        if config["basename"] is not None:
                            # Only process transformations with explicit basenames
                            basename = config["basename"].lower()
                            self.basenames.append(basename)
                            self.transformation_mappings[basename] = config
                            # logger.debug(
                            #     f"Loaded transformation mapping (basename): {basename} -> {name}"
                            # )
                        else:
                            # Ignore transformations with basename: null - no fallback
                            # transformations
                            logger.debug(
                                f"Ignoring transformation with basename: null: {name}"
                            )
                    elif "model_ids" in config:
                        # Legacy model_ids format - extract basenames for compatibility
                        for model_id in config["model_ids"]:
                            detected_basename = self._extract_basename_from_model_id(
                                model_id
                            )
                            if detected_basename:
                                basename_lower = detected_basename.lower()
                                self.basenames.append(basename_lower)
                                self.transformation_mappings[basename_lower] = config
                                logger.debug(
                                    f"Loaded transformation mapping (legacy): {basename_lower} -> {name}"
                                )

        except Exception as e:
            logger.error(
                f"Failed to load transformations from {transformations_file}: {e}"
            )

    def _extract_basename_from_model_id(self, model_id: str) -> str | None:
        """
        Extract basename from a model ID for legacy compatibility

        This is a simple heuristic that works for common patterns:
        - cursorcore-qw2-5-1-5b-q4-k-m -> cursorcore
        - deepseek-coder-33b-instruct-q4-k-m -> deepseek-coder
        - qwen2-5-coder-14b-instruct-q8-0 -> qwen2-5-coder
        """
        model_id_lower = model_id.lower()

        # Common basename patterns (ordered by specificity)
        known_patterns = [
            "deepseek-coder",
            "qwen2-5-coder",
            "wizard-vicuna",
            "starcoder2",
            "wizardcoder",
            "codellama",
            "qwen2-5",
            "llama3-1",
            "cursorcore",
        ]

        for pattern in known_patterns:
            if model_id_lower.startswith(pattern + "-") or model_id_lower == pattern:
                return pattern

        return None

    def find_basename(self, model_id: str) -> str | None:
        """
        Find matching basename using prefix matching with clean boundaries

        Args:
            model_id: The model ID to find basename for

        Returns:
            Matching basename or None if no match found

        Examples:
            "cursorcore-qw2-5-1-5b-q4-k-m" → "cursorcore"
            "deepseek-coder-33b-instruct-q4-k-m" → "deepseek-coder"
            "qwen2-5-coder-14b-instruct-q8-0" → "qwen2-5-coder" (not "qwen2-5")
        """
        if not model_id:
            return None

        model_id_lower = model_id.lower()

        # Longest-first matching (basenames are already sorted by length descending)
        for basename in self.basenames:
            # Check for exact match first
            if model_id_lower == basename:
                return basename

            # Check for prefix match with proper boundary detection
            if model_id_lower.startswith(basename + "-"):
                return basename

        logger.debug(f"No basename match found for model_id: {model_id}")
        return None

    def get_profile(self, model_id: str) -> dict | str | None:
        """
        Get profile config for model using basename matching

        Args:
            model_id: The model ID to get profile for

        Returns:
            Raw profile config (dict or str) or None if no match found.
            - Dict: Structured config (e.g., {vad_profile: "x", whisper_profile: "y"})
            - Str: Simple profile name (legacy format)
            - None: No profile found
        """
        basename = self.find_basename(model_id)
        if basename:
            profile = self.profile_mappings.get(basename)
            if profile is not None:
                profile_type = "dict" if isinstance(profile, dict) else "str"
                logger.debug(
                    f"Found profile for {model_id} via basename "
                    f"{basename}: {profile_type}"
                )
                return profile

        logger.debug(f"No profile found for model_id: {model_id}")
        return None

    def get_transformation(self, model_id: str) -> dict | None:
        """
        Get transformation config for model using basename matching

        Args:
            model_id: The model ID to get transformation for

        Returns:
            Transformation configuration dictionary or None if no match found.
            Only returns transformation if explicitly configured for model's basename.
            No fallback transformations - models use standard chat format by default.
        """
        basename = self.find_basename(model_id)
        if basename:
            # Model has a recognized basename - check for explicit transformation
            transformation = self.transformation_mappings.get(basename)
            if transformation:
                logger.debug(
                    f"Found explicit transformation for {model_id} via basename {basename}"
                )
                return transformation

        # No explicit transformation configured - use standard chat format
        logger.debug(
            f"No explicit transformation for model {model_id} - using standard chat format"
        )
        return None


# Singleton instance for convenience
_matcher_instance: UnifiedModelMatcher | None = None


def get_unified_matcher() -> UnifiedModelMatcher:
    """Get singleton matcher instance"""
    global _matcher_instance
    if _matcher_instance is None:
        _matcher_instance = UnifiedModelMatcher()
    return _matcher_instance


def get_model_profile(model_id: str) -> dict | str | None:
    """
    Get profile for model ID using unified basename matching

    Args:
        model_id: The model ID to get profile for

    Returns:
        Raw profile config (dict or str) or None if no match found
    """
    return get_unified_matcher().get_profile(model_id)


def get_model_profile_config(model_id: str) -> dict | str | None:
    """
    Get profile config for model ID using unified basename matching

    Alias for get_model_profile() for clarity when working with model defaults.

    Args:
        model_id: The model ID to get profile config for

    Returns:
        Raw profile config (dict or str) or None if no match found.
        - Dict: Structured config (e.g., {vad_profile: "x", whisper_profile: "y"})
        - Str: Simple profile name (legacy format)
        - None: No profile found
    """
    return get_unified_matcher().get_profile(model_id)


def get_model_transformation(model_id: str) -> dict | None:
    """
    Get transformation config for model ID using unified basename matching

    Args:
        model_id: The model ID to get transformation for

    Returns:
        Transformation configuration dictionary or None if no match found
    """
    return get_unified_matcher().get_transformation(model_id)
