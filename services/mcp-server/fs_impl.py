"""Unified ``fs`` implementation — single surface-aware chokepoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from endpoint_surface import Surface
from fs_roots import bind_workspaces_root, permission_refusal
from project_ops import workspaces_impl_registry
from tool_error_enricher import apply_life_sandbox_default, fs_missing_sandbox_hint
from tools.filesystem._cross_sandbox import copy_between_sandboxes_impl
from tools.filesystem._fs_dispatch import (
    SEARCH_MODES,
    dispatch_workspaces_op,
    validate_op_params,
)
from tools.filesystem._paths import FS_WORKFLOW_HINTS

if TYPE_CHECKING:
    from collections.abc import Callable

_VALID_ROOTS = frozenset({"cortex", "workspaces"})
_SANDBOX_TOOL: dict[str, str] = {"cortex": "files"}
_MD_OP_MAP: dict[str, str] = {
    "md_list": "list_sections",
    "md_read": "read_section",
    "md_to_dict": "to_dict",
    "md_replace": "replace_section",
    "md_append": "append_section",
    "md_insert": "insert_section",
    "md_delete": "delete_section",
}
_PATH_WRITE_OPS = frozenset(
    {
        "write",
        "append",
        "prepend",
        "replace",
        "insert_at_line",
        "write_binary",
        "append_binary",
        "delete",
        "md_replace",
        "md_append",
        "md_insert",
        "md_delete",
    }
)


def fs_impl(
    *,
    surface: Surface,
    overflow_registry: dict[str, Callable[..., Any]],
    op: str,
    sandbox: str,
    path: str,
    paths: list[str] | None,
    content: str,
    target: str,
    target_sandbox: str,
    line: int,
    section: str,
    all_occurrences: bool,
    include_untracked: bool,
    binary: bool,
    max_depth: int,
    offset: int,
    limit: int,
    expected_sha256: str,
    if_absent: bool,
    heading: str = "",
    level: int = 0,
    position: str = "",
    mode: str = "",
) -> dict[str, Any]:
    if not op:
        return {"error": "'op' is required"}
    if mode and mode not in SEARCH_MODES:
        return {
            "error": (
                f"Invalid mode: {mode!r}. Accepted values: auto (default — "
                "heuristic routing), content (force content regex search), "
                "filename (glob filename find, workspaces only)."
            )
        }

    ingress_meta: dict[str, Any] = {}
    effective_sandbox = apply_life_sandbox_default(
        surface=surface,
        sandbox=sandbox,
        path=path,
    )
    effective_path = path
    if path.strip():
        from implement_admission.closeout_helpers import cortex_files_root
        from implement_admission.scheme_resolve import resolve_fs_ingress

        try:
            ingress = resolve_fs_ingress(
                path,
                sandbox=effective_sandbox or None,
                cortex_root=cortex_files_root(),
                for_write=op in _PATH_WRITE_OPS,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        effective_sandbox = ingress.sandbox
        effective_path = ingress.rel_path
        if ingress.path_input_normalized:
            ingress_meta["path_input_normalized"] = True
        if ingress.normalization_advisory:
            ingress_meta["normalization_advisory"] = ingress.normalization_advisory

    if effective_sandbox not in _VALID_ROOTS:
        return {"error": fs_missing_sandbox_hint(path, surface=surface)}
    if target_sandbox and target_sandbox not in _VALID_ROOTS:
        return {
            "error": (
                "target_sandbox must be 'cortex' or 'workspaces', "
                f"got {target_sandbox!r}"
            )
        }

    refusal = permission_refusal(
        surface,
        effective_sandbox,
        op,
        target_sandbox=target_sandbox,
    )
    if refusal is not None:
        return refusal

    contract_error = validate_op_params(
        op,
        {
            "target": target,
            "section": section,
            "line": line,
            "heading": heading,
            "level": level,
            "position": position,
            "all_occurrences": all_occurrences,
            "mode": mode,
        },
    )
    if contract_error is not None:
        return contract_error

    if op.startswith("md_"):
        md_fn = overflow_registry.get("markdown")
        if md_fn is None:
            return {"error": "markdown tool not available"}
        md_op = _MD_OP_MAP.get(op)
        if md_op is None:
            valid = ", ".join(sorted(_MD_OP_MAP))
            return {"error": f"Unknown markdown op: {op!r}. Available: {valid}"}
        result = md_fn(
            op=md_op,
            path=effective_path,
            sandbox=effective_sandbox,
            section=section,
            content=content,
            heading=heading,
            level=level,
            position=position,
        )
        if isinstance(result, dict) and "error" not in result:
            result.update(ingress_meta)
        return result

    if op == "copy" and target_sandbox and target_sandbox != effective_sandbox:
        if not effective_path:
            return {"error": "'path' is required for copy"}
        if not target:
            return {"error": "'target' is required for copy"}
        result = copy_between_sandboxes_impl(
            effective_sandbox,
            effective_path,
            target_sandbox,
            target,
            surface=surface,
        )
        result["_next"] = FS_WORKFLOW_HINTS["copy"]
        result.update(ingress_meta)
        return result

    if effective_sandbox == "workspaces":
        impl_registry = {
            **workspaces_impl_registry(),
            **overflow_registry,
        }
        with bind_workspaces_root(surface):
            result = dispatch_workspaces_op(
                op,
                effective_path,
                paths,
                content,
                target,
                line,
                all_occurrences,
                include_untracked,
                binary,
                max_depth,
                offset,
                limit,
                impl_registry,
                FS_WORKFLOW_HINTS,
                mode=mode,
            )
        if isinstance(result, dict) and "error" not in result:
            result.update(ingress_meta)
        return result

    tool_name = _SANDBOX_TOOL[effective_sandbox]
    fn = overflow_registry.get(tool_name)
    if fn is None:
        return {"error": f"{tool_name} tool not available"}
    try:
        result = fn(
            op=op,
            path=effective_path,
            paths=paths or [],
            content=content,
            target=target,
            line=line,
            all_occurrences=all_occurrences,
            binary=binary,
            offset=offset,
            limit=limit,
            expected_sha256=expected_sha256,
            if_absent=if_absent,
            mode=mode,
        )
        if isinstance(result, dict) and "error" not in result:
            result.update(ingress_meta)
        return result
    except ValueError as exc:
        return {"error": str(exc)}
