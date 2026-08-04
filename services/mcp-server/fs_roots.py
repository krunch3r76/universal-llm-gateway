"""Filesystem root metadata and per-surface permission table.

Single chokepoint data for ``fs`` sandbox/op authorization and tool-description
derivation. Wire registration (``tools/list``) stays separate — see
``project_ops.workspaces_impl_registry`` for impl bindings decoupled from export.
"""

from __future__ import annotations

import os
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
    }
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


def permitted_ops(surface: Surface, root_id: str) -> frozenset[str]:
    """Return the op set authorized for *(surface, root_id)*."""
    return PERMISSIONS.get((surface, root_id), frozenset())


def is_op_permitted(surface: Surface, root_id: str, op: str) -> bool:
    return op in permitted_ops(surface, root_id)


def life_workspaces_read_granted() -> bool:
    """True when life may read workspaces (any read op in the grant)."""
    return bool(LIFE_WORKSPACES_READ_OPS & permitted_ops("life", "workspaces"))


def permission_refusal(
    surface: Surface,
    root_id: str,
    op: str,
    *,
    target_sandbox: str = "",
) -> dict[str, str] | None:
    """Return an error dict when *(surface, root_id, op)* is denied; else None."""
    if target_sandbox == "workspaces" and root_id != "workspaces":
        return _cross_sandbox_write_refusal(surface)
    allowed = permitted_ops(surface, root_id)
    if op in allowed:
        return None
    if root_id == "workspaces" and surface == "life":
        if op in _WRITE_OPS:
            return {
                "error": (
                    f"op={op!r} is not available for sandbox='workspaces' on the "
                    "/mcp/life surface — repository source is READ-ONLY here. "
                    f"Readable ops: {', '.join(sorted(allowed))}. Repository edits "
                    "are served on /mcp/code only; a life seat that needs a write "
                    "should request it from cursor rather than author it directly, "
                    "so the write stays inside the shared-checkout lease."
                )
            }
        readable = ", ".join(sorted(allowed))
        return {
            "error": (
                f"op={op!r} is not available for sandbox='workspaces' on the "
                f"/mcp/life surface — repository source is READ-ONLY here. "
                f"Readable ops: {readable}. Repository edits are served on "
                "/mcp/code only."
            )
        }
    write_lease = ROOTS.get(root_id, {}).get("write_lease", frozenset())
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


def _cross_sandbox_write_refusal(surface: Surface) -> dict[str, str]:
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
    if surface == "life":
        if life_ws_read:
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
        if life_ws_read:
            search_blurb = (
                "**Literal content search:** ``op=search`` is case-sensitive regex "
                "over file contents (cortex + read-only workspaces) — NOT semantic "
                "retrieval. Pattern goes in ``content=``. For meaning-based lookup "
                "use ``rag(op=search, …)`` or ``cortex(tool=search, …)``.\n\n"
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
