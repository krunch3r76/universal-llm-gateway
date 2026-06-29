"""Shared fs op-capability table and workspaces dispatch.

Single source of truth for which ops each sandbox supports. Both the
error-message generation and the workspaces dispatch ladder derive from
OP_SANDBOXES, preventing the three-way drift documented in bus thread 1177.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# ∀ sandbox-capability change: update only this table.
# The workspaces dispatcher and all error strings derive from it automatically.
# Shared ops (both sandboxes): read, read_multi, write, append, prepend,
#   replace, insert_at_line, list, delete, move, copy.
# Substrate-specific (capability flags, not forked ladders):
#   write_binary, append_binary → cortex only.
# search → both sandboxes (conversion-aware: PDF/DOCX/ODT/EML/HTML).
OP_SANDBOXES: dict[str, frozenset[str]] = {
    "read": frozenset({"cortex", "workspaces"}),
    "read_multi": frozenset({"cortex", "workspaces"}),
    "write": frozenset({"cortex", "workspaces"}),
    "append": frozenset({"cortex", "workspaces"}),
    "prepend": frozenset({"cortex", "workspaces"}),
    "replace": frozenset({"cortex", "workspaces"}),
    "insert_at_line": frozenset({"cortex", "workspaces"}),
    "list": frozenset({"cortex", "workspaces"}),
    "find": frozenset({"workspaces"}),
    "delete": frozenset({"cortex", "workspaces"}),
    "move": frozenset({"cortex", "workspaces"}),
    "copy": frozenset({"cortex", "workspaces"}),
    "search": frozenset({"cortex", "workspaces"}),
    "write_binary": frozenset({"cortex"}),
    "append_binary": frozenset({"cortex"}),
}


def sandbox_op_names(sandbox: str) -> str:
    """Return a sorted, comma-separated op list for the given sandbox."""
    return ", ".join(sorted(op for op, s in OP_SANDBOXES.items() if sandbox in s))


# Per-op descriptor metadata. Keys MUST match OP_SANDBOXES exactly (enforced by test).
OP_DOC: dict[str, tuple[str, str]] = {
    "read": (
        "(path, offset?, limit?)",
        "read file (text or PDF/DOCX/ODT/EML/HTML)",
    ),
    "read_multi": ("(paths: list)", "read multiple files"),
    "write": ("(path, content)", "write/create file"),
    "append": ("(path, content)", "append to file"),
    "prepend": ("(path, content)", "prepend to file"),
    "replace": ("(path, target, content, all_occurrences?)", "replace text"),
    "insert_at_line": ("(path, content, line)", "insert at line number"),
    "list": ("(path?, max_depth?)", "list directory (max_depth default 3)"),
    "find": ("(path?, content)", "glob filename find (workspaces only)"),
    "delete": ("(path)", "delete file"),
    "search": ("(path, content)", "regex content search; literal filenames → find"),
    "move": ("(path, target)", "rename/relocate file"),
    "copy": (
        "(path, target, target_sandbox?)",
        "copy file; when target_sandbox\n"
        "                           differs from sandbox, copy server-side between sandboxes",
    ),
    "write_binary": ("(path, content)", "write base64-encoded binary"),
    "append_binary": ("(path, content)", "append base64 chunk to binary file"),
}


def _sandbox_only_note(sandboxes: frozenset[str]) -> str:
    if sandboxes == frozenset({"workspaces"}):
        return " (workspaces sandbox only)"
    if sandboxes == frozenset({"cortex"}):
        return " (cortex sandbox only)"
    return ""


def sandbox_op_doc() -> str:
    """Build the fs tool docstring 'Standard ops' section from OP_SANDBOXES."""
    lines = ["        Standard ops:"]
    for op in sorted(OP_SANDBOXES):
        params, desc = OP_DOC[op]
        note = _sandbox_only_note(OP_SANDBOXES[op])
        if "\n" in desc:
            first, rest = desc.split("\n", 1)
            lines.append(f"          {op:<14} {params:<36} — {first}")
            lines.append(f"          {'':14} {'':36}   {rest}{note}")
        else:
            lines.append(f"          {op:<14} {params:<36} — {desc}{note}")
    return "\n".join(lines)


def md_section_op_doc() -> str:
    """Build the fs tool docstring 'Markdown section ops' section."""
    return (
        "Markdown section ops (for large docs):\n"
        "  md_list    (path)                    — list sections/TOC (PDFs: embedded outline; others: ATX markdown)\n"
        "  md_read    (path, section?)          — read one section; empty/absent section => full document (text/markdown; PDFs still require a section)\n"
        "  md_to_dict (path)                    — nested heading dict (PDFs: outline-driven; others: ATX sections)\n"
        "  md_replace (path, section, content)  — replace section body (text files only); content must NOT include the section heading — if it opens with a matching ATX heading, the op strips it and sets normalized_heading: true\n"
        "  md_append  (path, section, content)  — append to section body (text files only); same heading-less-content contract as md_replace\n"
        "  md_insert  (path, heading, level, position, section?, content?) — insert a new section (text files only); position: end|after|before; section is anchor for after/before; same heading-less-content contract as md_replace\n"
        "  md_delete  (path, section)           — delete section (text files only)\n"
        "Converted formats such as PDF are read-only for markdown section ops:\n"
        "use ``md_list`` / ``md_read`` to inspect them, not ``md_replace`` /\n"
        "``md_append`` / ``md_insert`` / ``md_delete``."
    )


def advertised_standard_ops() -> frozenset[str]:
    """Ops in the fs tool Standard ops section — derived from OP_SANDBOXES."""
    return frozenset(OP_SANDBOXES)


def unknown_op_error(op: str, sandbox: str) -> dict[str, str]:
    """Return a standard 'unknown op' error dict, derived from OP_SANDBOXES."""
    if op not in OP_SANDBOXES:
        return {"error": f"Unknown op: {op!r}. Available: {sandbox_op_names(sandbox)}"}
    return {
        "error": (
            f"Unknown {sandbox} op: {op!r}. Available: {sandbox_op_names(sandbox)}"
        )
    }


def dispatch_workspaces_op(
    op: str,
    path: str,
    paths: list[str] | None,
    content: str,
    target: str,
    line: int,
    all_occurrences: bool,
    include_untracked: bool,
    binary: bool,
    max_depth: int,
    offset: int,
    limit: int,
    overflow_registry: dict[str, Callable[..., Any]],
    workflow_hints: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch a workspaces-sandbox fs op via the project adapter.

    ∀ op ∈ OP_SANDBOXES ∧ "workspaces" ∈ OP_SANDBOXES[op]: handled here.
    ∀ op ∉ OP_SANDBOXES ∨ "workspaces" ∉ OP_SANDBOXES[op]: returns error dict.

    read_multi is implemented as map-over-read using the same
    {path: result_or_error} shape as the cortex read_files_batch_impl.
    """
    if op not in OP_SANDBOXES or "workspaces" not in OP_SANDBOXES[op]:
        return unknown_op_error(op, "workspaces")

    if op == "read":
        fn = overflow_registry.get("read_project_file")
        if fn is None:
            return {"error": "read_project_file tool not available"}
        return fn(path, binary=binary, offset=offset, limit=limit)

    if op == "read_multi":
        if not paths:
            return {"error": "'paths' is required for read_multi"}
        fn = overflow_registry.get("read_project_file")
        if fn is None:
            return {"error": "read_project_file tool not available"}
        results: dict[str, Any] = {}
        for p in paths:
            try:
                results[p] = fn(p, binary=binary)
            except Exception as exc:
                results[p] = {"error": str(exc)}
        return {"files": results}

    if op == "write":
        fn = overflow_registry.get("write_project_file")
        if fn is None:
            return {"error": "write_project_file tool not available"}
        return fn(path, content)

    if op == "list":
        fn = overflow_registry.get("list_project_files")
        if fn is None:
            return {"error": "list_project_files tool not available"}
        return fn(path, include_untracked=include_untracked, max_depth=max_depth)

    if op == "find":
        if not content:
            return {
                "error": (
                    "'content' is required for find and holds the filename or "
                    "glob pattern. Example: fs(op='find', sandbox='workspaces', "
                    "path='universal-llm-gateway', content='session_handoff.py')"
                )
            }
        fn = overflow_registry.get("find_project_files")
        if fn is None:
            return {"error": "find_project_files tool not available"}
        # max_depth is a list-oriented browse limit (default 3); a filename find
        # must reach matches at any depth, so it is not forwarded here. find runs
        # full-depth like search (friction 13196).
        return fn(content, directory=path)

    if op == "search":
        if not content:
            return {
                "error": (
                    "'content' is required for search and holds the "
                    "regex query string. Example: fs(op='search', "
                    "sandbox='workspaces', "
                    "path='universal-llm-gateway', "
                    "content='Error occurred')"
                )
            }
        fn = overflow_registry.get("search_project_files")
        if fn is None:
            return {"error": "search_project_files tool not available"}
        return fn(content, directory=path, include_untracked=include_untracked)

    if op in {"append", "prepend"}:
        fn = overflow_registry.get("edit_project_file")
        if fn is None:
            return {"error": "edit_project_file tool not available"}
        return fn(path, op, content)

    if op == "replace":
        fn = overflow_registry.get("edit_project_file")
        if fn is None:
            return {"error": "edit_project_file tool not available"}
        return fn(
            path,
            "replace",
            content,
            target_str=target,
            all_occurrences=all_occurrences,
        )

    if op == "insert_at_line":
        fn = overflow_registry.get("edit_project_file")
        if fn is None:
            return {"error": "edit_project_file tool not available"}
        return fn(path, "insert_at_line", content, line=line)

    if op == "move":
        fn = overflow_registry.get("move_project_file")
        if fn is None:
            return {"error": "move_project_file tool not available"}
        return fn(path, target)

    if op == "copy":
        fn = overflow_registry.get("copy_project_file")
        if fn is None:
            return {"error": "copy_project_file tool not available"}
        return fn(path, target)

    if op == "delete":
        if not path:
            return {"error": "'path' is required for delete"}
        fn = overflow_registry.get("delete_project_file")
        if fn is None:
            return {"error": "delete_project_file tool not available"}
        result = fn(path)
        if "error" not in result:
            result["_next"] = workflow_hints["delete_workspaces"]
        return result

    return unknown_op_error(op, "workspaces")  # pragma: no cover
