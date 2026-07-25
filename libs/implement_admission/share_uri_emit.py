"""Share URI egress — canonical ``workspaces://`` / ``cortex://`` emission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.scheme_resolve import repo_base, workspaces_root
from implement_admission.share_uri_registry import is_cortex_entity_uri, leading_segment


def _repo_dirs(root: Path) -> list[Path]:
    if (root / ".git").exists():
        return [root]
    try:
        children = [child for child in sorted(root.iterdir()) if child.is_dir()]
    except OSError:
        return [root]
    repos = [child for child in children if (child / ".git").exists()]
    return repos or [root]


def sandbox_rel(
    sandbox: str,
    abs_path: Path,
    *,
    workspaces_root_override: Path | None = None,
    cortex_root: Path | None = None,
) -> str:
    """Return sandbox-relative posix path for *abs_path*."""
    from implement_admission.closeout_helpers import cortex_files_root

    if sandbox == "cortex":
        root = (cortex_root or cortex_files_root()).resolve()
    elif sandbox == "workspaces":
        root = workspaces_root(workspaces_root_override).resolve()
    else:
        raise ValueError(f"Unknown sandbox {sandbox!r}")

    resolved = abs_path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        if sandbox == "workspaces":
            repo = repo_base(root)
            rel = resolved.relative_to(repo.resolve()).as_posix()
            if repo.name != root.name:
                return f"{repo.name}/{rel}"
            return rel
        raise


def to_share_uri(
    sandbox: str,
    path_or_rel: str | Path,
    *,
    workspaces_root_override: Path | None = None,
    cortex_root: Path | None = None,
) -> str:
    """Emit canonical Share URI for *sandbox* + relative or absolute path."""
    if isinstance(path_or_rel, Path):
        rel = sandbox_rel(
            sandbox,
            path_or_rel,
            workspaces_root_override=workspaces_root_override,
            cortex_root=cortex_root,
        )
    else:
        raw = str(path_or_rel).strip().lstrip("/")
        if sandbox == "workspaces" and "/" in raw:
            first = raw.split("/", 1)[0]
            ws_root = workspaces_root(workspaces_root_override).resolve()
            if first in {repo.name for repo in _repo_dirs(ws_root)}:
                rel = raw
            else:
                repo = repo_base(ws_root)
                rel = f"{repo.name}/{raw}" if repo.name != ws_root.name else raw
        else:
            rel = raw

    clean = rel.lstrip("/")
    first = leading_segment(clean)
    if first and ":" in first:
        raise ValueError(
            f"Refuse to mint Share URI with ':' in leading segment {first!r}; "
            "colon forces entity form and must not appear in file-root egress."
        )

    if sandbox == "cortex":
        return f"cortex://{clean}"
    if sandbox == "workspaces":
        return f"workspaces://{clean}"
    raise ValueError(f"Unknown sandbox {sandbox!r}")


def dual_carry(
    sandbox: str,
    rel_path: str,
    *,
    workspaces_root_override: Path | None = None,
    cortex_root: Path | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build agent-facing payload with sandbox-relative ``path`` + canonical ``uri``."""
    clean_rel = rel_path.lstrip("/")
    payload: dict[str, Any] = {
        "path": clean_rel,
        "uri": to_share_uri(
            sandbox,
            clean_rel,
            workspaces_root_override=workspaces_root_override,
            cortex_root=cortex_root,
        ),
    }
    payload.update(extra)
    return payload


def project_share_uri_for_abs(
    abs_path: Path,
    *,
    workspaces_root_override: Path | None = None,
) -> str:
    """``workspaces://{repo}/{rel}`` for an absolute workspaces file."""
    ws_root = workspaces_root(workspaces_root_override).resolve()
    resolved = abs_path.resolve()
    candidates = _repo_dirs(ws_root)
    nested = repo_base(ws_root)
    if nested not in candidates and nested.is_dir():
        candidates = [nested, *candidates]
    for repo in candidates:
        try:
            rel = resolved.relative_to(repo.resolve()).as_posix()
            return f"workspaces://{repo.name}/{rel}"
        except ValueError:
            continue
    rel = sandbox_rel("workspaces", resolved, workspaces_root_override=ws_root)
    return f"workspaces://{rel}"


__all__ = [
    "dual_carry",
    "is_cortex_entity_uri",
    "project_share_uri_for_abs",
    "sandbox_rel",
    "to_share_uri",
]
