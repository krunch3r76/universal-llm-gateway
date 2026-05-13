"""Server-side copies between MCP filesystem sandboxes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from mcp_events import record
from universal_logging import get_logger

from ._paths import _SANDBOX_ROOT

logger = get_logger(__name__)


def _resolve_project_root() -> Path:
    """Resolve the workspaces sandbox root, logging a loud error on default fallback.

    The default ``/data/project`` must match the host bind mount that exposes
    the workspaces tree to the MCP container; if it diverges,
    ``copy_between_sandboxes_impl`` will write/read outside the expected
    sandbox boundary. Per ``[quality:defaults]`` (architecture-invariants.md):
    every default that is not explicitly user-configured emits an
    ERROR-level signal so the silent-default footgun is observable.
    """
    configured = os.environ.get("PROJECT_ROOT")
    if configured:
        return Path(configured)
    fallback = Path("/data/project")
    logger.error(
        "PROJECT_ROOT is unset — falling back to %s for cross-sandbox copy. "
        "Set PROJECT_ROOT explicitly to the host workspaces bind mount or "
        "cross-sandbox copies will resolve outside the workspaces sandbox.",
        fallback,
    )
    return fallback


_SANDBOX_ROOTS: dict[str, Path] = {
    "cortex": _SANDBOX_ROOT,
    "workspaces": _resolve_project_root(),
}


def _resolve_sandbox_path(sandbox: str, relative: str) -> Path:
    """Resolve a sandbox-relative path without allowing traversal or symlink escape."""
    try:
        root = _SANDBOX_ROOTS[sandbox].resolve()
    except KeyError:
        valid = ", ".join(sorted(_SANDBOX_ROOTS))
        raise ValueError(f"Unknown sandbox: {sandbox!r}. Available: {valid}") from None

    candidate = (root / relative.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(
            f"Path {relative!r} resolves outside {sandbox!r} sandbox; traversal rejected"
        ) from None
    return candidate


def copy_between_sandboxes_impl(
    source_sandbox: str,
    source: str,
    target_sandbox: str,
    destination: str,
) -> dict[str, str]:
    """Copy one file between two sandboxes without materializing bytes in MCP."""
    if source_sandbox == target_sandbox:
        raise ValueError(
            "Cross-sandbox copy requires different source and target sandboxes"
        )

    src = _resolve_sandbox_path(source_sandbox, source)
    dst = _resolve_sandbox_path(target_sandbox, destination)
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {source!r}")
    if not src.is_file():
        raise ValueError(f"Source is not a file: {source!r}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    record(
        "mcp.tool.file.copied",
        source_sandbox=source_sandbox,
        source=source,
        target_sandbox=target_sandbox,
        destination=destination,
    )
    return {
        "status": "copied",
        "source_sandbox": source_sandbox,
        "target_sandbox": target_sandbox,
        "from": str(src),
        "to": str(dst),
    }
