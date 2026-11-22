"""Server-side copies between MCP filesystem sandboxes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from endpoint_surface import Surface
from fs_roots import fs_root_for
from implement_admission.scheme_resolve import resolve_fs_ingress
from implement_admission.share_uri_emit import to_share_uri
from mcp_events import record

from tools._durable_write import (
    PreImageMismatchError,
    SourceDigestMismatchError,
    WriteVerifyError,
    write_verify_error_dict,
)

from tools.filesystem._ops_paths import (
    CopyRefusalError,
    copy_file_verified,
    pre_image_mismatch_dict,
    source_digest_mismatch_dict,
)
from lane_branch_root import GATEWAY_REPO_DIRNAME
from tools.filesystem._paths import SANDBOX_ROOT
from tools.filesystem._share_uri_response import attach_dual_carry


def _sandbox_root(sandbox: str, *, surface: Surface = "code") -> Path:
    if sandbox == "workspaces":
        return fs_root_for(surface, "workspaces").resolve()
    return SANDBOX_ROOT.resolve()


def _resolve_lane_source_path(
    source_root: Path,
    relative: str,
    *,
    surface: Surface = "code",
) -> tuple[Path, str]:
    root = source_root.resolve()
    ingress = resolve_fs_ingress(
        relative,
        sandbox="workspaces",
        cortex_root=_sandbox_root("cortex", surface=surface),
        workspaces_root_override=root,
        for_write=False,
    )
    if ingress.sandbox != "workspaces":
        raise ValueError(
            f"Path {relative!r} resolves to sandbox {ingress.sandbox!r}, "
            f"expected 'workspaces'"
        )
    rel = ingress.rel_path.lstrip("/")
    repo_prefix = f"{GATEWAY_REPO_DIRNAME}/"
    if rel.startswith(repo_prefix):
        rel = rel[len(repo_prefix) :]
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(
            f"Path {relative!r} resolves outside lane worktree; traversal rejected"
        ) from None
    if not candidate.is_file():
        raise ValueError(f"Source is not a file: {relative!r}")
    return candidate, rel


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
    source_root: Path | None = None,
    expected_read_sha256: str | None = None,
    if_absent: bool = False,
    expected_sha256: str | None = None,
    lane_fields: dict[str, str] | None = None,
) -> dict[str, str]:
    """Copy one file between two sandboxes without materializing bytes in MCP."""
    if source_sandbox == target_sandbox:
        raise ValueError(
            "Cross-sandbox copy requires different source and target sandboxes"
        )

    if source_root is not None:
        src, src_rel = _resolve_lane_source_path(
            source_root, source, surface=surface
        )
    else:
        src, src_rel = _resolve_sandbox_path(
            source_sandbox, source, for_write=False, surface=surface
        )
    dst, dst_rel = _resolve_sandbox_path(
        target_sandbox, destination, for_write=True, surface=surface
    )
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {source!r}")
    if not src.is_file():
        raise ValueError(f"Source is not a file: {source!r}")

    event_lane: dict[str, Any] = {}
    if lane_fields:
        event_lane = {
            k: lane_fields[k]
            for k in ("lane_thread", "lane_branch")
            if k in lane_fields
        }

    try:
        dest, digest, nbytes, replaced = copy_file_verified(
            src,
            dst,
            dest_rel=dst_rel,
            if_absent=if_absent,
            expected_sha256=expected_sha256 or None,
            expected_source_digest=expected_read_sha256 or None,
        )
    except CopyRefusalError as exc:
        record(
            "mcp.tool.file.copy_rejected",
            source_sandbox=source_sandbox,
            source=source,
            target_sandbox=target_sandbox,
            destination=dst_rel,
            reason=exc.payload.get("reason", "copy_rejected"),
            **event_lane,
        )
        return exc.payload
    except SourceDigestMismatchError as exc:
        record(
            "mcp.tool.file.copy_rejected",
            source_sandbox=source_sandbox,
            source=source,
            target_sandbox=target_sandbox,
            destination=dst_rel,
            reason=exc.reason,
            **event_lane,
        )
        return source_digest_mismatch_dict(exc)
    except PreImageMismatchError as exc:
        record(
            "mcp.tool.file.copy_rejected",
            source_sandbox=source_sandbox,
            source=source,
            target_sandbox=target_sandbox,
            destination=dst_rel,
            reason=exc.reason,
            **event_lane,
        )
        return pre_image_mismatch_dict(exc)
    except WriteVerifyError as exc:
        return write_verify_error_dict(exc)

    dest_rel = dest.relative_to(
        _sandbox_root(target_sandbox, surface=surface)
    ).as_posix()
    record(
        "mcp.tool.file.copied",
        source_sandbox=source_sandbox,
        source=source,
        target_sandbox=target_sandbox,
        destination=dest_rel,
        read_sha256=digest,
        bytes=nbytes,
        **event_lane,
    )
    result: dict[str, Any] = {
        "status": "copied",
        "source_sandbox": source_sandbox,
        "target_sandbox": target_sandbox,
        "from": src_rel,
        "to": dest_rel,
        "from_uri": to_share_uri(source_sandbox, src_rel),
        "to_uri": to_share_uri(target_sandbox, dest_rel),
        "read_sha256": digest,
        "bytes": nbytes,
    }
    if replaced is not None:
        result["replaced_sha256"] = replaced
    if lane_fields:
        result.update(lane_fields)
    return attach_dual_carry(
        result,
        sandbox=target_sandbox,
        rel_path=dest_rel,
    )