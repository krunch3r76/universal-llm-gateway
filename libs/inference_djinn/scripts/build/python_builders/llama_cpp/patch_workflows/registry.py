"""Registry for llama-cpp-python patch workflows."""

from __future__ import annotations

import importlib
import logging
import re
from pathlib import Path

from .config import PatchWorkflow

logger = logging.getLogger(__name__)

# Global registry instance
_REGISTRY: PatchRegistry | None = None


class PatchRegistry:
    """
    Registry of patch workflows keyed by version pattern.

    Usage:
        registry = get_registry()
        workflow = registry.find_workflow("0.3.16")
    """

    def __init__(self) -> None:
        self._workflows: dict[str, PatchWorkflow] = {}

    def register(self, workflow: PatchWorkflow) -> None:
        """Register a patch workflow."""
        self._workflows[workflow.name] = workflow
        logger.debug(f"Registered patch workflow: {workflow.name}")

    def find_workflow(self, version: str) -> PatchWorkflow | None:
        """
        Find matching workflow for a version string.

        Args:
            version: Normalized version string (e.g., "0.3.16")

        Returns:
            Matching PatchWorkflow or None
        """
        for workflow in self._workflows.values():
            if re.match(workflow.version_pattern, version):
                return workflow
        return None

    def normalize_version(self, raw_version: str) -> str:
        """
        Normalize version string for matching.

        Handles formats like "v0.3.16", "0.3.16", commit hashes, etc.
        """
        # Strip leading 'v'
        version = raw_version.lstrip("v")

        # If it looks like a commit hash, return as-is
        if re.match(r"^[a-f0-9]{7,40}$", version):
            return version

        # Extract version numbers
        match = re.match(r"^(\d+\.\d+\.\d+)", version)
        if match:
            return match.group(1)

        return version


def get_registry() -> PatchRegistry:
    """Get or create the global patch registry."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = PatchRegistry()
        _load_workflows(_REGISTRY)
    return _REGISTRY


def _load_workflows(registry: PatchRegistry) -> None:
    """Auto-discover and load workflow modules from workflows/ directory."""
    workflows_dir = Path(__file__).parent / "workflows"

    if not workflows_dir.exists():
        logger.warning(f"Workflows directory not found: {workflows_dir}")
        return

    for module_path in workflows_dir.glob("*.py"):
        if module_path.name.startswith("_"):
            continue

        module_name = module_path.stem
        try:
            # Import relative to patch_workflows package
            module = importlib.import_module(
                f".workflows.{module_name}",
                package="patch_workflows",
            )
            if hasattr(module, "register"):
                module.register(registry)
                logger.debug(f"Loaded workflow module: {module_name}")
        except Exception as e:
            logger.warning(f"Failed to load workflow {module_name}: {e}")
