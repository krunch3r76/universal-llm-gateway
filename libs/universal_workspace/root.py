"""Workspace root detection for unified workspace."""

import os
from functools import lru_cache
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_workspace_root() -> Path:
    """
    Find workspace root using deterministic markers.

    Searches upward from current directory for workspace root indicators:
    - .git directory (git repository root)
    - config/models/ static catalog directory (or legacy config/model_catalog.yaml)
    - Combination of services/ and libs/ directories

    Returns:
        Path to workspace root

    Raises:
        RuntimeError: If workspace root cannot be determined after exhaustive search
    """
    # Allow override via environment variable (for testing/deployment)
    if workspace_override := os.getenv("WORKSPACE_ROOT"):
        path = Path(workspace_override).resolve()
        # Use print for early debugging (logger may not be configured yet)
        print(
            f"[DEBUG] WORKSPACE_ROOT env var set to: {workspace_override} (resolved: {path})",
            flush=True,
        )
        if _is_workspace_root(path):
            logger.info(f"Using WORKSPACE_ROOT env var: {path}")
            return path
        # Print detailed error since logger.error may not show in early startup
        print(
            f"[ERROR] WORKSPACE_ROOT env var points to invalid workspace root: {workspace_override}\n"
            + f"  Resolved path: {path}\n"
            + "  The path exists but doesn't satisfy workspace markers.\n"
            + "  Falling back to current directory search...",
            flush=True,
        )
        logger.error(
            f"WORKSPACE_ROOT env var points to invalid workspace root: {workspace_override}\n"
            + f"Resolved path: {path}\n"
            + "The path exists but doesn't satisfy workspace markers (see debug logs above).\n"
            + "Falling back to current directory search..."
        )

    current = Path.cwd().resolve()

    # Check current directory first
    if _is_workspace_root(current):
        return current

    # Search upward (max 10 levels to prevent infinite loops)
    for _ in range(10):
        if current.parent == current:  # Reached filesystem root
            break
        current = current.parent
        if _is_workspace_root(current):
            return current

    # Exhaustive search failed
    raise RuntimeError(
        "Cannot determine workspace root. Expected markers:\n"
        + "  - .git directory\n"
        + "  - config/models/ directory (or legacy config/model_catalog.yaml)\n"
        + "  - services/ and libs/ directories\n"
        + f"Searched from: {Path.cwd().resolve()}"
    )


def _is_workspace_root(path: Path) -> bool:
    """
    Check if path is workspace root using deterministic markers.

    Must satisfy at least 2 of 3 criteria:
    1. Contains .git directory
    2. Contains config/models/ (or legacy config/model_catalog.yaml)
    3. Contains both services/ and libs/ directories
    """
    markers_found = 0
    markers_status = []

    # Marker 1: .git directory
    git_exists = (path / ".git").exists()
    if git_exists:
        markers_found += 1
        markers_status.append("✅ .git")
    else:
        markers_status.append("❌ .git")

    # Marker 2: Static catalog layout (prefer split catalog; accept legacy monolith)
    models_dir_exists = (path / "config" / "models").is_dir()
    legacy_catalog_exists = (path / "config" / "model_catalog.yaml").exists()
    catalog_exists = models_dir_exists or legacy_catalog_exists
    if catalog_exists:
        markers_found += 1
        if models_dir_exists and legacy_catalog_exists:
            markers_status.append(
                "✅ config/models/ (legacy config/model_catalog.yaml also present)"
            )
        elif models_dir_exists:
            markers_status.append("✅ config/models/")
        else:
            markers_status.append("✅ legacy config/model_catalog.yaml")
    else:
        markers_status.append("❌ config/models/ (or legacy config/model_catalog.yaml)")

    # Marker 3: Workspace structure (services/ and libs/)
    services_exists = (path / "services").is_dir()
    libs_exists = (path / "libs").is_dir()
    if services_exists and libs_exists:
        markers_found += 1
        markers_status.append("✅ services/ and libs/")
    else:
        markers_status.append(
            f"❌ services/ and libs/ (services={services_exists}, libs={libs_exists})"
        )

    # Log validation result
    is_valid = markers_found >= 2

    # Print for early debugging (before logging is configured)
    debug_msg = (
        f"[DEBUG] Workspace validation for {path}: {markers_found}/3 markers - {'VALID' if is_valid else 'INVALID'}\n"
        + "\n".join(f"  {status}" for status in markers_status)
    )
    print(debug_msg, flush=True)

    # Also log if logger is available
    logger.debug(
        f"Workspace validation for {path}: {markers_found}/3 markers found - {'VALID' if is_valid else 'INVALID'}\n"
        + "\n".join(f"  {status}" for status in markers_status)
    )

    # Require at least 2 markers for confidence
    return is_valid


def get_static_catalog_path() -> Path:
    """
    Get path to static model catalog.

    Returns:
        Path to config/model_catalog.yaml in workspace root

    Raises:
        RuntimeError: If workspace root cannot be determined
        FileNotFoundError: If catalog file doesn't exist at expected location
    """
    catalog_path = get_workspace_root() / "config" / "model_catalog.yaml"

    if not catalog_path.exists():
        raise FileNotFoundError(
            f"Static catalog not found at: {catalog_path}\n"
            + f"Workspace root: {get_workspace_root()}\n"
            + "Ensure catalog file has been moved to workspace root."
        )

    return catalog_path
