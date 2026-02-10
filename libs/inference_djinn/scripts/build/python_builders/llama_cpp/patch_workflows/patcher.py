"""Versioned patcher for llama-cpp-python source."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import PatchDefinition, PatchWorkflow
from .registry import PatchRegistry, get_registry

logger = logging.getLogger(__name__)


class LlamaCppPatcher:
    """
    Apply patches to llama-cpp-python source based on version.

    Usage:
        patcher = LlamaCppPatcher(
            source_dir=Path("/tmp/llama-cpp-python"),
            version="0.3.16",
        )
        patcher.apply_patches()
    """

    def __init__(
        self,
        source_dir: Path,
        version: str,
        *,
        force_patches: bool = False,
        registry: PatchRegistry | None = None,
    ) -> None:
        """
        Initialize patcher.

        Args:
            source_dir: llama-cpp-python source directory
            version: Version string (raw, will be normalized)
            force_patches: If True, apply even unverified patches
            registry: Custom registry (defaults to global)
        """
        self.source_dir = Path(source_dir)
        self.force_patches = force_patches

        self._registry = registry or get_registry()
        self._raw_version = version
        self._version = self._registry.normalize_version(version)
        self._workflow: PatchWorkflow | None = None
        self._patches_applied = 0
        self._patches_skipped = 0

    def apply_patches(self) -> bool:
        """
        Apply patches based on version workflow.

        Returns:
            True if patches were applied (or none needed),
            False if skipped due to gating (unverified version).
        """
        self._workflow = self._registry.find_workflow(self._version)

        if self._workflow is None:
            logger.info(f"⚙️  No patch workflow for llama-cpp-python {self._version}")
            return True

        if not self._workflow.verified_working and not self.force_patches:
            logger.warning(
                f"⚠️  Patches for llama-cpp-python {self._version} are EXPERIMENTAL"
            )
            logger.warning(f"   Workflow: {self._workflow.name}")
            logger.warning("   Patches skipped (use --force-patches to apply)")
            if self._workflow.notes:
                logger.warning(f"   Note: {self._workflow.notes}")
            return False

        logger.info(f"🔧 Applying patches for llama-cpp-python {self._version}...")
        logger.info(f"   Workflow: {self._workflow.name}")

        if not self._workflow.verified_working:
            logger.warning("   ⚠️  EXPERIMENTAL: patches forced")

        for patch in self._workflow.patches:
            self._apply_single_patch(patch)

        logger.info(
            f"   ✅ Patches complete: {self._patches_applied} applied, "
            f"{self._patches_skipped} skipped"
        )
        return True

    def _apply_single_patch(self, patch: PatchDefinition) -> None:
        """Apply a single patch definition."""
        target_file = self.source_dir / patch.file_path

        if not target_file.exists():
            if patch.optional:
                logger.debug(f"   ⚠️  {patch.file_path} not found (optional)")
            else:
                logger.warning(f"   ⚠️  {patch.file_path} not found")
            self._patches_skipped += 1
            return

        try:
            content = target_file.read_text()

            if patch.old_pattern not in content:
                if patch.optional:
                    logger.debug(
                        f"   ⚠️  Pattern not found in {patch.file_path} "
                        "(may already be patched)"
                    )
                else:
                    logger.warning(
                        f"   ⚠️  Pattern not found in {patch.file_path}"
                    )
                self._patches_skipped += 1
                return

            # Create backup
            backup_path = target_file.with_suffix(target_file.suffix + ".orig")
            if not backup_path.exists():
                backup_path.write_text(content)

            # Apply patch
            content = content.replace(patch.old_pattern, patch.new_template)
            target_file.write_text(content)

            logger.info(f"      ✅ {patch.description}")
            self._patches_applied += 1

        except Exception as e:
            logger.warning(f"   ⚠️  Failed to patch {patch.file_path}: {e}")
            self._patches_skipped += 1
