"""Server-side copies between MCP filesystem sandboxes."""

from __future__ import annotations

from pathlib import Path

from endpoint_surface import Surface
from fs_roots import fs_root_for
from implement_admission.scheme_resolve import resolve_fs_ingress
from implement_admission.share_uri_emit import to_share_uri
from mcp_events import record

from tools.filesystem._ops_paths import copy_file_verified
from tools.filesystem._paths import SANDBOX_ROOT
from tools.filesystem._share_uri_response import attach_dual_carry


def _sandbox_root(sandbox: str, *, surface: Surface = "code") -> Path:
    if sandbox == "workspaces":
        return fs_root_for(surface, "workspaces").resolve()
    return SANDBOX_ROOT.resolve()


def _resolve_sandbox_path(
    sandbox: str,
    relative: str,
    *,
    for_write: bool = False,
    surface: Surface = "code",
) -> tuple[Path, str]:
    """Resolve path via shared ingress; return (absolute, sandbox-relative rel)."""
    root = _sandbox_root(sandbox, surface=surface)
    ingress = resolve_fs_ingress(
        relative,
        sandbox=sandbox,
        cortex_root=_sandbox_root("cortex", surface=surface),
        workspaces_root_override=root if sandbox == "workspaces" else None,
        for_write=for_write,
    )
    if ingress.sandbox != sandbox:
        raise ValueError(
            f"Path {relative!r} resolves to sandbox {ingress.sandbox!r}, "
            f"expected {sandbox!r}"
        )
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
    *,
    surface: Surface = "code",
) -> dict[str, str]:
    """Copy one file between two sandboxes without materializing bytes in MCP."""
    if source_sandbox == target_sandbox:
        raise ValueError(
            "Cross-sandbox copy requires different source and target sandboxes"
        )

    src, src_rel = _resolve_sandbox_path(
        source_sandbox, source, for_write=False, surface=surface
    )
    dst, _dst_rel = _resolve_sandbox_path(
        target_sandbox, destination, for_write=True, surface=surface
    )
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {source!r}")
    if not src.is_file():
        raise ValueError(f"Source is not a file: {source!r}")

    dest, digest = copy_file_verified(src, dst)
    dest_rel = dest.relative_to(
        _sandbox_root(target_sandbox, surface=surface)
    ).as_posix()
    record(
        "mcp.tool.file.copied",
        source_sandbox=source_sandbox,
        source=source,
        target_sandbox=target_sandbox,
        destination=dest_rel,
    )
    return attach_dual_carry(
        {
            "status": "copied",
            "source_sandbox": source_sandbox,
            "target_sandbox": target_sandbox,
            "from": src_rel,
            "to": dest_rel,
            "from_uri": to_share_uri(source_sandbox, src_rel),
            "to_uri": to_share_uri(target_sandbox, dest_rel),
            "read_sha256": digest,
        },
        sandbox=target_sandbox,
        rel_path=dest_rel,
    )
