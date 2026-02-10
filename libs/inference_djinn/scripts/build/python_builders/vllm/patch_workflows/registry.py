"""Patch workflow registry for version-based workflow lookup."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import PatchWorkflow

logger = logging.getLogger(__name__)


class PatchRegistry:
    """
    Registry of patch workflows indexed by vLLM version patterns.

    Provides version matching to find the appropriate workflow for a given
    vLLM version, respecting verified_working status for gating.
    """

    def __init__(self) -> None:
        self._workflows: list[PatchWorkflow] = []

    def register(self, workflow: PatchWorkflow) -> None:
        """Register a patch workflow."""
        self._workflows.append(workflow)
        logger.debug(f"Registered patch workflow: {workflow}")

    def find_workflow(self, version: str) -> PatchWorkflow | None:
        """
        Find matching workflow for a vLLM version.

        Args:
            version: Normalized version string (e.g., "0.13.2", "0.14.1")

        Returns:
            Matching PatchWorkflow or None if no workflow matches.
        """
        for workflow in self._workflows:
            if re.match(workflow.version_pattern, version):
                logger.debug(f"Version {version} matched workflow: {workflow.name}")
                return workflow

        logger.debug(f"No workflow found for version: {version}")
        return None

    def list_workflows(self) -> list[PatchWorkflow]:
        """Return all registered workflows."""
        return list(self._workflows)

    @staticmethod
    def normalize_version(raw_version: str) -> str:
        """
        Normalize vLLM version string for matching.

        Handles various formats:
        - "v0.13.2" -> "0.13.2"
        - "0.14.1.dev0+gb17039bcc" -> "0.14.1"
        - "main" -> "99.99.99" (always unverified)

        Args:
            raw_version: Version string from git tag, wheel, or CLI

        Returns:
            Normalized version string (X.Y.Z format)
        """
        if raw_version == "main" or raw_version.startswith("origin/"):
            # Main branch = bleeding edge, always unverified
            return "99.99.99"

        # Strip 'v' prefix
        version = raw_version.lstrip("v")

        # Extract base version (before .dev, +, or other suffixes)
        # Match: X.Y.Z where X, Y, Z are digits
        match = re.match(r"(\d+\.\d+\.\d+)", version)
        if match:
            return match.group(1)

        # Fallback: try X.Y format
        match = re.match(r"(\d+\.\d+)", version)
        if match:
            return f"{match.group(1)}.0"

        # Unknown format, return as-is
        logger.warning(f"Could not normalize version: {raw_version}")
        return version


# Global registry instance
_registry: PatchRegistry | None = None


def get_registry() -> PatchRegistry:
    """Get the global patch registry, initializing workflows on first access."""
    global _registry
    if _registry is None:
        _registry = PatchRegistry()
        # Import workflows to trigger registration
        from .workflows import register_all_workflows

        register_all_workflows(_registry)
    return _registry
