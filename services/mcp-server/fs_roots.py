"""Filesystem root metadata and per-surface permission table.

Single chokepoint data for ``fs`` sandbox/op authorization and tool-description
derivation. Wire registration (``tools/list``) stays separate — see
``project_ops.workspaces_impl_registry`` for impl bindings decoupled from export.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from endpoint_surface import Surface
from tools.filesystem._fs_dispatch import OP_SANDBOXES

# Read-only workspaces ops the /mcp/life surface may serve. Same membership as
# the pre-table allowlist — reads break operator curation monopoly; writes stay
# outside the shared-checkout write lease (see write_lease on workspaces root).
LIFE_WORKSPACES_READ_OPS: frozenset[str] = frozenset(
    {
        "read",
        "read_multi",
        "list",
        "find",
        "search",
        "md_read",
        "md_list",
        "md_to_dict",
        "recent_commits",
    }
)

# Explicit life workspaces write grant when ``LIFE_PROJECT_ROOT`` is enabled.
# Destructive ops (delete, move) and raw ``_WORKSPACES_OPS`` inheritance are
# intentionally excluded — see arc 6655 bind shape (c).
LIFE_WORKSPACES_WRITE_OPS: frozenset[str] = frozenset(
    {
        "write",
        "append",
        "prepend",
        "replace",
        "insert_at_line",
        "copy",
        "md_replace",
        "md_append",
        "md_insert",
        "md_delete",
    }
)

LIFE_WORKSPACES_ENABLED_OPS: frozenset[str] = (
    LIFE_WORKSPACES_READ_OPS | LIFE_WORKSPACES_WRITE_OPS
)

_WRITE_OPS = frozenset(
    op for op, sandboxes in OP_SANDBOXES.items() if "workspaces" in sandboxes
) - LIFE_WORKSPACES_READ_OPS

_CORTEX_OPS = frozenset(
    op for op, sandboxes in OP_SANDBOXES.items() if "cortex" in sandboxes
)
_WORKSPACES_OPS = frozenset(
    op for op, sandboxes in OP_SANDBOXES.items() if "workspaces" in sandboxes
)

ROOTS: dict[str, dict[str, Any]] = {
    "cortex": {
        "fs_root": Path("/data/files"),
        "uri_scheme": "cortex",
        "write_lease": frozenset({"life", "code"}),
    },
    "workspaces": {
        "fs_root": Path(os.environ.get("PROJECT_ROOT", "/data/project")),
        "uri_scheme": "workspaces",
        "write_lease": frozenset({"code"}),
    },
}

PERMISSIONS: dict[tuple[Surface, str], frozenset[str]] = {
    ("life", "cortex"): _CORTEX_OPS,
    ("life", "workspaces"): LIFE_WORKSPACES_READ_OPS,
    ("code", "cortex"): _CORTEX_OPS,
    ("code", "workspaces"): _WORKSPACES_OPS,
}


def project_root_path() -> Path:
    """Shared checkout root (``PROJECT_ROOT``)."""
    return Path(os.environ.get("PROJECT_ROOT", "/data/project")).resolve()


def life_project_root_path() -> Path | None:
    """Configured life worktree root, or None when unset."""
    configured = os.environ.get("LIFE_PROJECT_ROOT")
    if not configured:
        return None
    return Path(configured).resolve()


def life_workspaces_write_enabled() -> bool:
    """True when life may write workspaces into a distinct ``LIFE_PROJECT_ROOT``."""
    life_root = life_project_root_path()
    if life_root is None:
        return False
    return life_root != project_root_path()


def workspaces_write_lease() -> frozenset[str]:
    """Surfaces authorized to write workspaces for the current configuration."""
    lease = frozenset({"code"})
    if life_workspaces_write_enabled():
        return lease | frozenset({"life"})
    return lease


def fs_root_for(surface: Surface, root_id: str) -> Path:
    """Resolve the on-disk root for *(surface, root_id)*."""
    if root_id == "workspaces":
        if surface == "life" and life_workspaces_write_enabled():
            return life_project_root_path()  # type: ignore[return-value]
        return project_root_path()
    meta = ROOTS.get(root_id, {})
    fs_root = meta.get("fs_root")
    if isinstance(fs_root, Path):
        return fs_root.resolve()
    return Path(str(fs_root)).resolve()


def permitted_ops(surface: Surface, root_id: str) -> frozenset[str]:
    """Return the op set authorized for *(surface, root_id)*."""
    if surface == "life" and root_id == "workspaces":
        if life_workspaces_write_enabled():
            return LIFE_WORKSPACES_ENABLED_OPS
        return LIFE_WORKSPACES_READ_OPS
    return PERMISSIONS.get((surface, root_id), frozenset())


def is_op_permitted(surface: Surface, root_id: str, op: str) -> bool:
    return op in permitted_ops(surface, root_id)


def life_workspaces_read_granted() -> bool:
    """True when life may read workspaces (any read op in the grant)."""
    return bool(LIFE_WORKSPACES_READ_OPS & permitted_ops("life", "workspaces"))


def life_workspaces_write_refusal_message() -> str:
    """Standard life-surface workspaces write refusal (table-derived)."""
    allowed = permitted_ops("life", "workspaces")
    allowed_text = ", ".join(sorted(allowed))
    if life_workspaces_write_enabled():
        return (
            f"op='write' is not available for sandbox='workspaces' on the "
            f"/mcp/life surface — not in the life workspaces grant. "
            f"Permitted ops: {allowed_text}."
        )
    return (
        f"op='write' is not available for sandbox='workspaces' on the "
        "/mcp/life surface — repository source is READ-ONLY here. "
        f"Readable ops: {allowed_text}. Repository edits "
        "are served on /mcp/code only; a life seat that needs a write "
        "should request it from cursor rather than author it directly, "
        "so the write stays inside the shared-checkout lease."
    )


def _git_head_at(repo: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    sha = proc.stdout.decode("utf-8", errors="replace").strip()
    return sha or None


def workspaces_git_head(root: Path, rel_path: str = "") -> tuple[str | None, str | None]:
    """Return ``(head_sha, unknown_reason)`` for a workspaces root + optional rel path."""
    root = root.resolve()
    if (root / ".git").exists():
        head = _git_head_at(root)
        if head:
            return head, None
        return None, "git rev-parse HEAD failed at workspaces root"

    if rel_path.strip():
        from tools._project_paths import candidate_paths

        for candidate in candidate_paths(rel_path, root):
            repo = candidate.parent if candidate.is_file() else candidate
            while repo != root and repo != repo.parent:
                if (repo / ".git").exists():
                    head = _git_head_at(repo)
                    if head:
                        return head, None
                    return None, f"git rev-parse HEAD failed at {repo}"
                repo = repo.parent

    return None, "workspaces root is not a git repository"


def life_workspaces_resolution_fields(
    surface: Surface,
    root: Path,
    *,
    rel_path: str = "",
) -> dict[str, str]:
    """Structured root + HEAD visibility for successful life workspaces responses."""
    if surface != "life":
        return {}
    fields: dict[str, str] = {"workspaces_resolved_root": str(root.resolve())}
    head, unknown = workspaces_git_head(root, rel_path)
    if head:
        fields["workspaces_read_at_head"] = head
    else:
        fields["workspaces_head_unknown"] = unknown or "HEAD could not be resolved"
    return fields


@contextmanager
def bind_workspaces_root(surface: Surface) -> Iterator[Path]:
    """Bind project path resolution to ``fs_root_for(surface, 'workspaces')``."""
    root = fs_root_for(surface, "workspaces")
    import tools._project_paths as paths_mod
    import tools.markdown_tool as markdown_mod
    import tools.project as project_mod

    old_project = project_mod._PROJECT_ROOT
    old_paths = paths_mod._PROJECT_ROOT
    old_markdown = markdown_mod._PROJECT_ROOT
    project_mod._PROJECT_ROOT = root
    paths_mod._PROJECT_ROOT = root
    markdown_mod._PROJECT_ROOT = root
    try:
        yield root
    finally:
        project_mod._PROJECT_ROOT = old_project
        paths_mod._PROJECT_ROOT = old_paths
        markdown_mod._PROJECT_ROOT = old_markdown


def permission_refusal(
    surface: Surface,
    root_id: str,
    op: str,
    *,
    target_sandbox: str = "",
) -> dict[str, str] | None:
    """Return an error dict when *(surface, root_id, op)* is denied; else None."""
    if target_sandbox == "workspaces" and root_id != "workspaces":
        refusal = _cross_sandbox_write_refusal(surface)
        if refusal is not None:
            return refusal
    allowed = permitted_ops(surface, root_id)
    if op in allowed:
        return None
    if root_id == "workspaces" and surface == "life":
        readable = ", ".join(sorted(allowed))
        if life_workspaces_write_enabled():
            return {
                "error": (
                    f"op={op!r} is not available for sandbox='workspaces' on the "
                    f"/mcp/life surface — not in the life workspaces grant. "
                    f"Permitted ops: {readable}."
                )
            }
        if op in _WRITE_OPS or op in LIFE_WORKSPACES_WRITE_OPS:
            return {"error": life_workspaces_write_refusal_message()}
        return {
            "error": (
                f"op={op!r} is not available for sandbox='workspaces' on the "
                f"/mcp/life surface — repository source is READ-ONLY here. "
                f"Readable ops: {readable}. Repository edits are served on "
                "/mcp/code only."
            )
        }
    write_lease = (
        workspaces_write_lease()
        if root_id == "workspaces"
        else ROOTS.get(root_id, {}).get("write_lease", frozenset())
    )
    if op in _WRITE_OPS and surface not in write_lease:
        return {
            "error": (
                f"op={op!r} is not available for sandbox={root_id!r} on the "
                f"/mcp/{surface} surface — write lease held by "
                f"{', '.join(sorted(write_lease))} only."
            )
        }
    if root_id == "cortex":
        readable = ", ".join(sorted(allowed))
        return {
            "error": (
                f"op={op!r} is not available for sandbox='cortex' on the "
                f"/mcp/{surface} surface. "
                f"Permitted ops: {readable}. "
                'Path may still exist — try fs(op="read"). '
                "Refusal is op_not_permitted, not path_absent."
            )
        }
    return {
        "error": (
            f"op={op!r} is not available for sandbox={root_id!r} on the "
            f"/mcp/{surface} surface."
        )
    }


def _cross_sandbox_write_refusal(surface: Surface) -> dict[str, str] | None:
    if surface == "life" and life_workspaces_write_enabled():
        return None
    return {
        "error": (
            "target_sandbox='workspaces' is not writable from the /mcp/life "
            "surface. Repository source is readable here and editable on "
            "/mcp/code only. For agent-process artifacts (specs, packets, "
            "closeouts, sidecars), omit sandbox (defaults to cortex) or use a "
            "cortex:// Share URI. Life-seat handoff packets must carry a "
            "cortex:// sidecar mirror."
        )
    }


def derive_fs_sandbox_intro(surface: Surface) -> tuple[str, str, str]:
    """Build fs intro/find/search blurbs from PERMISSIONS — not hand-edited."""
    life_ws_read = surface == "life" and life_workspaces_read_granted()
    life_ws_write = surface == "life" and life_workspaces_write_enabled()
    if surface == "life":
        if life_ws_write:
            allowed = ", ".join(sorted(permitted_ops("life", "workspaces")))
            sandbox_intro = (
                "File I/O for cortex and life worktree repository source on "
                "/mcp/life. `op` is REQUIRED.\n"
                "On this surface, an unqualified relative path defaults to cortex "
                "when `sandbox` is omitted; `cortex://{rel}` is the canonical form "
                "for durable agent-process artifacts. Repository source (`workspaces`) "
                "writes land in the configured life worktree (`LIFE_PROJECT_ROOT`), "
                "not the shared checkout.\n\n"
                f"Workspaces ops on /mcp/life: {allowed}.\n\n"
                "Share URI grammar (canonical cross-resident refs on this surface):\n"
                "  cortex://{rel}             — notes, agent-skills, dropbox, uploads\n"
                "  workspaces://{repo}/{rel}  — repository source (life worktree)\n\n"
                "Examples:\n"
                '  fs(op="write", path="cortex://notes/system/threads/foo.md", content="...")\n'
                '  fs(op="write", sandbox="workspaces", path="universal-llm-gateway/tmp/foo.md", content="...")\n'
                '  fs(op="read", path="workspaces://universal-llm-gateway/libs/foo.py")\n\n'
            )
            find_blurb = (
                "``find`` and ``search`` ``mode=filename`` are available on "
                "workspaces from /mcp/life.\n\n"
            )
            search_blurb = (
                "**Literal content search:** ``op=search`` is case-sensitive regex "
                "over file contents (cortex + workspaces) — NOT semantic "
                "retrieval. Pattern goes in ``content=``. For meaning-based lookup "
                "use ``rag(op=search, …)`` or ``cortex(tool=search, …)``.\n\n"
            )
        elif life_ws_read:
            readable = ", ".join(sorted(LIFE_WORKSPACES_READ_OPS))
            sandbox_intro = (
                "File I/O for cortex and read-only repository source on "
                "/mcp/life. `op` is REQUIRED.\n"
                "On this surface, an unqualified relative path defaults to cortex "
                "when `sandbox` is omitted; `cortex://{rel}` is the canonical form "
                "for durable writes. Repository source (`workspaces`) is "
                "READ-ONLY here — use /mcp/code for edits.\n\n"
                f"Workspaces-readable ops on /mcp/life: {readable}.\n\n"
                "Share URI grammar (canonical cross-resident refs on this surface):\n"
                "  cortex://{rel}             — notes, agent-skills, dropbox, uploads\n"
                "  workspaces://{repo}/{rel}  — repository source (read-only)\n\n"
                "Examples:\n"
                '  fs(op="write", path="cortex://notes/system/threads/foo.md", content="...")\n'
                '  fs(op="read", sandbox="workspaces", path="universal-llm-gateway/README.md")\n'
                '  fs(op="read", path="workspaces://universal-llm-gateway/libs/foo.py")\n'
                '  fs(sandbox="cortex", op="read", path="notes/system/specs/foo.md")\n\n'
            )
            find_blurb = (
                "``find`` and ``search`` ``mode=filename`` are available on "
                "workspaces (read-only) from /mcp/life.\n\n"
            )
            search_blurb = (
                "**Literal content search:** ``op=search`` is case-sensitive regex "
                "over file contents (cortex + read-only workspaces) — NOT semantic "
                "retrieval. Pattern goes in ``content=``. For meaning-based lookup "
                "use ``rag(op=search, …)`` or ``cortex(tool=search, …)``.\n\n"
            )
        else:
            sandbox_intro = (
                "File I/O for the cortex sandbox on /mcp/life. `op` is REQUIRED.\n"
                "On this surface, an unqualified relative path defaults to cortex "
                "when `sandbox` is omitted; `cortex://{rel}` is the canonical form. "
                "Repository source (`workspaces`) is not available here — use "
                "/mcp/code for workspaces:// paths; mirror agent-process artifacts "
                "to cortex:// for life-seat handoffs.\n\n"
                "Share URI grammar (canonical cross-resident refs on this surface):\n"
                "  cortex://{rel}             — notes, agent-skills, dropbox, uploads\n\n"
                "Examples:\n"
                '  fs(op="write", path="cortex://notes/system/threads/foo.md", content="...")\n'
                '  fs(sandbox="cortex", op="read", path="notes/system/specs/foo.md")\n\n'
            )
            find_blurb = (
                "``find`` (filename glob) and ``search`` ``mode=filename`` are "
                "/mcp/code (/ workspaces) capabilities only — not on /mcp/life.\n\n"
            )
            search_blurb = (
                "**Literal content search (cortex):** ``op=search`` scans file "
                "*contents* with a case-sensitive regex — NOT semantic retrieval. "
                "Pass the pattern in ``content`` (not ``pattern``). ``path`` may "
                "be a file or directory. ``mode`` accepts ``auto`` or "
                "``content`` only; ``filename`` is rejected on cortex. For "
                "meaning-based lookup use ``rag(op=search, …)`` or "
                "``cortex(tool=search, …)`` — not ``fs`` search.\n\n"
            )
    else:
        sandbox_intro = (
            "File I/O across sandboxes (cortex, workspaces). `op` is REQUIRED; "
            "`sandbox` is optional when `path` carries a Share URI scheme.\n\n"
            "Share URI grammar (canonical cross-resident refs):\n"
            "  workspaces://{repo}/{rel}  — repository source, tasks, docs\n"
            "  cortex://{rel}             — notes, agent-skills, dropbox, uploads\n\n"
            "Examples:\n"
            '  fs(op="read", path="cortex://notes/system/specs/foo.md")\n'
            '  fs(op="read", path="workspaces://universal-llm-gateway/tasks/specs/foo.md")\n'
            '  fs(sandbox="workspaces", op="read", path="universal-llm-gateway/libs/foo.py")\n\n'
        )
        find_blurb = (
            "``find`` (workspaces only): locate files by name/glob — use instead of\n"
            "``search`` for filenames. ``search`` scans file *contents* with a regex.\n\n"
        )
        search_blurb = (
            "**Literal content search:** ``op=search`` is case-sensitive regex "
            "over file contents (cortex + workspaces) — NOT semantic retrieval. "
            "Pattern goes in ``content=``. For meaning-based lookup use "
            "``rag(op=search, …)`` or ``cortex(tool=search, …)``.\n\n"
        )
    return sandbox_intro, find_blurb, search_blurb
