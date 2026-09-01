"""Op-availability parity tests for OP_SANDBOXES (bus thread 1177, A2 + CF-3 gates).

∀ op ∈ OP_SANDBOXES:
  "workspaces" ∈ OP_SANDBOXES[op] ⟹ dispatch_workspaces_op returns ¬unknown_op_error
  "workspaces" ∉ OP_SANDBOXES[op] ⟹ dispatch_workspaces_op returns unknown_op_error exactly

Descriptor parity (CF-3):
  OP_DOC keys == OP_SANDBOXES keys
  advertised_standard_ops() == frozenset(OP_SANDBOXES)
  sandbox_op_doc() mentions exactly the table ops (no drift, no orphans)
"""

from __future__ import annotations

import re

import pytest

from tools.filesystem._fs_dispatch import (
    MD_OPS,
    OP_DOC,
    OP_SANDBOXES,
    advertised_standard_ops,
    dispatch_workspaces_op,
    sandbox_op_doc,
    unknown_op_error,
)

_WF_HINTS: dict = {"delete_workspaces": "stub_hint"}


def _stub_fn(*_args: object, **_kwargs: object) -> dict:
    return {"status": "stub_ok"}


_STUB_REGISTRY: dict = {
    "read_project_file": _stub_fn,
    "write_project_file": _stub_fn,
    "list_project_files": _stub_fn,
    "search_project_files": _stub_fn,
    "find_project_files": _stub_fn,
    "edit_project_file": _stub_fn,
    "move_project_file": _stub_fn,
    "copy_project_file": _stub_fn,
    "delete_project_file": _stub_fn,
}

# Minimal args sufficient to pass per-op required-param guards without actually
# invoking filesystem I/O. cortex-only ops need no valid args (rejected before dispatch).
_OP_ARGS: dict[str, dict] = {
    "read": {"path": "a.md"},
    "read_multi": {"paths": ["a.md"]},
    "write": {"path": "a.md", "content": "x"},
    "append": {"path": "a.md", "content": "x"},
    "prepend": {"path": "a.md", "content": "x"},
    "replace": {"path": "a.md", "target": "x", "content": "y"},
    "insert_at_line": {"path": "a.md", "line": 1, "content": "x"},
    "list": {"path": ""},
    "delete": {"path": "a.md"},
    "move": {"path": "a.md", "target": "b.md"},
    "copy": {"path": "a.md", "target": "b.md"},
    "search": {"content": "query"},
    "find": {"content": "*.py"},
    "recent_commits": {"path": "universal-llm-gateway"},
    "write_binary": {},
    "append_binary": {},
}


def _call(op: str) -> dict:
    kwargs = _OP_ARGS.get(op, {})
    return dispatch_workspaces_op(
        op=op,
        path=kwargs.get("path", ""),
        paths=kwargs.get("paths", None),
        content=kwargs.get("content", ""),
        target=kwargs.get("target", ""),
        line=kwargs.get("line", 0),
        all_occurrences=False,
        include_untracked=False,
        binary=False,
        max_depth=3,
        offset=kwargs.get("offset", 0),
        limit=kwargs.get("limit", 0),
        overflow_registry=_STUB_REGISTRY,
        workflow_hints=_WF_HINTS,
    )


@pytest.mark.parametrize(
    "op",
    sorted(
        op
        for op in OP_SANDBOXES
        if "workspaces" in OP_SANDBOXES[op] and op not in MD_OPS
    ),
)
def test_advertised_workspaces_op_dispatches(op: str) -> None:
    """Ops advertising workspaces must not return unknown_op_error.

    md_* ops dispatch via fs_impl's markdown overflow path, not here.
    """
    result = _call(op)
    err = result.get("error", "")
    assert "Unknown" not in err, (
        f"op={op!r} is in OP_SANDBOXES['workspaces'] but dispatch returned: {result}"
    )


@pytest.mark.parametrize(
    "op",
    sorted(op for op in OP_SANDBOXES if "workspaces" not in OP_SANDBOXES[op]),
)
def test_substrate_only_rejects_cleanly_on_workspaces(op: str) -> None:
    """Cortex-only ops must return unknown_op_error exactly when dispatched for workspaces."""
    result = _call(op)
    assert result == unknown_op_error(op, "workspaces"), (
        f"op={op!r} not in OP_SANDBOXES['workspaces'] but got unexpected: {result}"
    )


def test_op_doc_keys_match_table() -> None:
    """OP_DOC metadata must cover exactly OP_SANDBOXES — no orphans either way."""
    assert frozenset(OP_DOC) == frozenset(OP_SANDBOXES)


def test_search_available_on_both_sandboxes() -> None:
    """search is conversion-aware on cortex and workspaces (fs-search-converted-docs F6)."""
    assert OP_SANDBOXES["search"] == frozenset({"cortex", "workspaces"})


def test_search_op_doc_states_literal_regex() -> None:
    """search descriptor must distinguish literal regex from semantic retrieval."""
    _params, desc = OP_DOC["search"]
    lowered = desc.lower()
    assert "regex" in lowered
    assert "semantic" in lowered
    assert "content" in lowered


def test_advertised_ops_match_table() -> None:
    """advertised_standard_ops() must mirror OP_SANDBOXES."""
    assert advertised_standard_ops() == frozenset(OP_SANDBOXES)


def test_ranged_read_does_not_return_unknown_op_error() -> None:
    """Ranged read must dispatch through workspaces without unknown_op_error."""
    result = dispatch_workspaces_op(
        op="read",
        path="a.md",
        paths=None,
        content="",
        target="",
        line=0,
        all_occurrences=False,
        include_untracked=False,
        binary=False,
        max_depth=3,
        offset=1,
        limit=1,
        overflow_registry=_STUB_REGISTRY,
        workflow_hints=_WF_HINTS,
    )
    err = result.get("error", "")
    assert "Unknown" not in err, f"ranged read dispatch failed: {result}"


def test_descriptor_doc_lists_exactly_table_ops() -> None:
    """Generated fs Standard ops section lists non-md table ops (md in md_section_op_doc)."""
    doc = sandbox_op_doc()
    mentioned = set(re.findall(r"^\s+(\w+)\s+\(", doc, re.MULTILINE))
    standard_ops = set(OP_SANDBOXES) - MD_OPS
    assert mentioned == standard_ops, (
        f"descriptor/table mismatch: extra={mentioned - standard_ops!r} "
        f"missing={standard_ops - mentioned!r}"
    )


WRITE_EDIT_OPS = frozenset(
    {
        "write",
        "append",
        "prepend",
        "replace",
        "insert_at_line",
        "write_binary",
        "append_binary",
    }
)


def test_fs_descriptor_lists_each_md_op_once() -> None:
    """Combined fs descriptor must mention each md op exactly once (Standard + md sections)."""
    from fs_description import build_fs_tool_description

    for surface in ("life", "code"):
        desc = build_fs_tool_description(surface)
        md_line_ops = re.findall(r"^\s+(md_\w+)\s+\(", desc, re.MULTILINE)
        assert frozenset(md_line_ops) == MD_OPS, (
            f"md section ops mismatch on {surface}: {md_line_ops!r}"
        )
        assert len(md_line_ops) == len(MD_OPS), (
            f"duplicate md op lines on {surface}: {md_line_ops!r}"
        )
        standard_block = sandbox_op_doc()
        for op in MD_OPS:
            assert op not in standard_block, f"{op} leaked into Standard ops section"

    """Every durable write/edit op must be in OP_SANDBOXES with expected sandboxes."""
    assert WRITE_EDIT_OPS <= frozenset(OP_SANDBOXES)
    assert OP_SANDBOXES["write"] == frozenset({"cortex", "workspaces"})
    assert OP_SANDBOXES["replace"] == frozenset({"cortex", "workspaces"})
    assert OP_SANDBOXES["write_binary"] == frozenset({"cortex"})
    assert OP_SANDBOXES["append_binary"] == frozenset({"cortex"})
