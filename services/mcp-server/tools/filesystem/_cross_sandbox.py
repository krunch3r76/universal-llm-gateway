"""Server-side copies between MCP filesystem sandboxes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from implement_admission.scheme_resolve import resolve_fs_ingress
from implement_admission.share_uri_emit import to_share_uri
from mcp_events import record
from universal_logging import get_logger

from ._paths import SANDBOX_ROOT

logger = get_logger(__name__)


def _resolve_project_root() -> Path:
    """Resolve the workspaces sandbox root, logging a loud error on default fallback."""
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
    "cortex": SANDBOX_ROOT,
    "workspaces": _resolve_project_root(),
}


def _resolve_sandbox_path(
    sandbox: str, relative: str, *, for_write: bool = False
) -> tuple[Path, str]:
    """Resolve path via shared ingress; return (absolute, sandbox-relative rel)."""
    ingress = resolve_fs_ingress(
        relative,
        sandbox=sandbox,
        cortex_root=_SANDBOX_ROOTS["cortex"],
        for_write=for_write,
    )
    if ingress.sandbox != sandbox:
        raise ValueError(
            f"Path {relative!r} resolves to sandbox {ingress.sandbox!r}, "
            f"expected {sandbox!r}"
        )
    root = _SANDBOX_ROOTS[sandbox].resolve()
    candidate = (root / ingress.rel_path.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(
            f"Path {relative!r} resolves outside {sandbox!r} sandbox; traversal rejected"
        ) from None
    return candidate, ingress.rel_path.lstrip("/")


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

    src, src_rel = _resolve_sandbox_path(source_sandbox, source, for_write=False)
    dst, dst_rel = _resolve_sandbox_path(target_sandbox, destination, for_write=True)
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
        "from": src_rel,
        "to": dst_rel,
        "from_uri": to_share_uri(source_sandbox, src_rel),
        "to_uri": to_share_uri(target_sandbox, dst_rel),
    }
