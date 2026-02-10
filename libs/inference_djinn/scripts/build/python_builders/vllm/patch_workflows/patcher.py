"""Versioned patcher that applies workflow-defined patches."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import PatchDefinition, PatchWorkflow
from .registry import PatchRegistry, get_registry

logger = logging.getLogger(__name__)


class VersionedPatcher:
    """
    Apply patches based on vLLM version using workflow configurations.

    This replaces the hardcoded ArchitecturePatcher with a version-aware
    system that applies only patches verified to work for each version.
    """

    def __init__(
        self,
        source_dir: Path,
        gpu_arch: str,
        vllm_version: str,
        *,
        force_patches: bool = False,
        registry: PatchRegistry | None = None,
    ) -> None:
        """
        Initialize versioned patcher.

        Args:
            source_dir: vLLM source directory
            gpu_arch: Target GPU architecture code (e.g., "120" for SM_120)
            vllm_version: vLLM version string (raw, will be normalized)
            force_patches: If True, apply patches even for unverified versions
            registry: Custom registry (defaults to global registry)

        Raises:
            ValueError: If gpu_arch is invalid (less than 2 digits)
        """
        if len(gpu_arch) < 2:
            raise ValueError(
                f"Invalid GPU architecture: '{gpu_arch}'. "
                f"Must be at least 2 digits (e.g., 89, 120)."
            )

        self.source_dir = Path(source_dir)
        self.gpu_arch = gpu_arch
        self.arch_dotted = f"{gpu_arch[:-1]}.{gpu_arch[-1]}"  # "120" -> "12.0"
        self.force_patches = force_patches

        self._registry = registry or get_registry()
        self._raw_version = vllm_version
        self._version = self._registry.normalize_version(vllm_version)
        self._workflow: PatchWorkflow | None = None
        self._patches_applied = 0
        self._patches_skipped = 0

    def apply_patches(self) -> bool:
        """
        Apply patches based on version workflow.

        Returns:
            True if patches were applied (or skipped intentionally),
            False if patches were skipped due to gating (unverified version).
        """
        # Find matching workflow
        self._workflow = self._registry.find_workflow(self._version)

        if self._workflow is None:
            logger.info(f"⚙️  No patch workflow for vLLM {self._version}")
            logger.info("   Using vLLM default build configuration")
            return True  # Not an error, just no patches

        # Check gating
        if not self._workflow.verified_working and not self.force_patches:
            logger.warning(f"⚠️  Patches for vLLM {self._version} are EXPERIMENTAL")
            logger.warning(f"   Workflow: {self._workflow.name}")
            logger.warning("   Patches skipped (use --apply-patches to force)")
            if self._workflow.notes:
                logger.warning(f"   Note: {self._workflow.notes}")
            return False

        # Apply patches
        logger.info(f"🔧 Applying patches for vLLM {self._version}...")
        logger.info(f"   Workflow: {self._workflow.name}")
        logger.info(f"   Target: SM_{self.gpu_arch} (exclusive)")

        if not self._workflow.verified_working:
            logger.warning("   ⚠️  EXPERIMENTAL: patches forced with --apply-patches")

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
                logger.debug(f"   ⚠️  {patch.file_path} not found (optional, skipping)")
            else:
                logger.warning(f"   ⚠️  {patch.file_path} not found (skipping)")
            self._patches_skipped += 1
            return

        try:
            content = target_file.read_text()

            if patch.old_pattern not in content:
                if patch.optional:
                    logger.debug(
                        f"   ⚠️  Pattern not found in {patch.file_path} "
                        "(optional, may already be patched)"
                    )
                else:
                    logger.warning(
                        f"   ⚠️  Pattern not found in {patch.file_path} "
                        "(may already be patched or version mismatch)"
                    )
                self._patches_skipped += 1
                return

            # Create backup
            backup_path = target_file.with_suffix(target_file.suffix + ".orig")
            if not backup_path.exists():
                backup_path.write_text(content)

            # Apply template substitution
            new_content = patch.new_template.format(
                gpu_arch=self.gpu_arch,
                arch_dotted=self.arch_dotted,
            )

            # Replace pattern
            content = content.replace(patch.old_pattern, new_content)
            target_file.write_text(content)

            logger.info(f"      ✅ {patch.description}")
            self._patches_applied += 1

        except Exception as e:
            logger.warning(f"   ⚠️  Failed to patch {patch.file_path}: {e}")
            self._patches_skipped += 1
