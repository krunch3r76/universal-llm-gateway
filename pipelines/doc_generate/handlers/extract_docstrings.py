"""
Deterministic extractor for doc-generate.

Reads a Python subsystem directory, extracts docstrings/signatures/imports using
tree-sitter-python, and attaches any existing architecture document content.
"""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import tree_sitter as _ts
import tree_sitter_python as _tspython
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_PY_LANG = _ts.Language(_tspython.language())
_PY_PARSER = _ts.Parser(_PY_LANG)
_STRING_NODE_TYPES = {"string", "concatenated_string"}
_DEF_NODE_TYPES = {"function_definition", "async_function_definition"}


def _decode(node: _ts.Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _string_node_to_text(node: _ts.Node, source: bytes) -> str:
    literal = _decode(node, source)
    try:
        value = ast.literal_eval(literal)
    except (SyntaxError, ValueError):
        return literal.strip()
    if isinstance(value, str):
        return value
    return str(value)


def _extract_docstring_from_block(block_node: _ts.Node | None, source: bytes) -> str:
    if block_node is None:
        return ""
    for child in block_node.children:
        if child.type != "expression_statement":
            continue
        if not child.children:
            return ""
        maybe_string = child.children[0]
        if maybe_string.type in _STRING_NODE_TYPES:
            return _string_node_to_text(maybe_string, source).strip()
        return ""
    return ""


def _signature(node: _ts.Node, source: bytes) -> str:
    body = node.child_by_field_name("body")
    end_byte = body.start_byte if body is not None else node.end_byte
    text = source[node.start_byte : end_byte].decode("utf-8", errors="replace").rstrip()
    if text.endswith(":"):
        text = text[:-1]
    return " ".join(text.split())


def _extract_imports(module_node: _ts.Node, source: bytes) -> list[str]:
    imports: list[str] = []
    for child in module_node.children:
        if child.type in {"import_statement", "import_from_statement"}:
            imports.append(_decode(child, source).strip())
    return imports


def _extract_class_methods(class_node: _ts.Node, source: bytes) -> list[dict[str, Any]]:
    methods: list[dict[str, Any]] = []
    body = class_node.child_by_field_name("body")
    if body is None:
        return methods
    for member in body.children:
        if member.type not in _DEF_NODE_TYPES:
            continue
        method_name_node = member.child_by_field_name("name")
        if method_name_node is None:
            continue
        methods.append(
            {
                "name": _decode(method_name_node, source),
                "signature": _signature(member, source),
                "docstring": _extract_docstring_from_block(
                    member.child_by_field_name("body"), source
                ),
                "line": member.start_point[0] + 1,
            }
        )
    return methods


def _relative_path(path: Path, anchor: Path) -> str:
    try:
        return path.relative_to(anchor).as_posix()
    except ValueError:
        return path.as_posix()


def _extract_file_inventory(py_file: Path, workspace_root: Path) -> dict[str, Any]:
    source = py_file.read_bytes()
    tree = _PY_PARSER.parse(source)
    module_node = tree.root_node
    module_docstring = _extract_docstring_from_block(module_node, source)

    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    for child in module_node.children:
        if child.type == "class_definition":
            class_name_node = child.child_by_field_name("name")
            if class_name_node is None:
                continue
            classes.append(
                {
                    "name": _decode(class_name_node, source),
                    "signature": _signature(child, source),
                    "docstring": _extract_docstring_from_block(
                        child.child_by_field_name("body"), source
                    ),
                    "line": child.start_point[0] + 1,
                    "methods": _extract_class_methods(child, source),
                }
            )
            continue
        if child.type in _DEF_NODE_TYPES:
            fn_name_node = child.child_by_field_name("name")
            if fn_name_node is None:
                continue
            functions.append(
                {
                    "name": _decode(fn_name_node, source),
                    "signature": _signature(child, source),
                    "docstring": _extract_docstring_from_block(
                        child.child_by_field_name("body"), source
                    ),
                    "line": child.start_point[0] + 1,
                }
            )

    return {
        "path": _relative_path(py_file, workspace_root),
        "module_docstring": module_docstring,
        "imports": _extract_imports(module_node, source),
        "classes": classes,
        "functions": functions,
    }


class ExtractDocstringsHandler(BaseHandler):
    """Extract docstring inventory for a subsystem directory."""

    step_type: str = "doc_generate_extract_docstrings"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        start_time = time.monotonic()
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}

        subsystem_path_value = self._resolve_input(
            resolver, step, "subsystem_path", inputs
        )
        subsystem_path_raw = str(subsystem_path_value).strip()
        if not subsystem_path_raw:
            return StepOutput(
                raw=json.dumps({"error": "subsystem_path is empty"}),
                json={"error": "subsystem_path is empty"},
                step_id=step.id,
                error="subsystem_path is empty",
            )

        workspace_root = Path.cwd()
        target_dir = Path(subsystem_path_raw)
        if not target_dir.is_absolute():
            target_dir = (workspace_root / target_dir).resolve()
        else:
            target_dir = target_dir.resolve()

        if not target_dir.exists() or not target_dir.is_dir():
            msg = f"subsystem_path is not a directory: {subsystem_path_raw}"
            logger.error("Step '%s': %s", step.id, msg)
            return StepOutput(
                raw=json.dumps({"error": msg}),
                json={"error": msg},
                step_id=step.id,
                error=msg,
            )

        py_files = sorted(target_dir.rglob("*.py"))
        modules: list[dict[str, Any]] = []
        classes: list[dict[str, Any]] = []
        functions: list[dict[str, Any]] = []
        imports: list[dict[str, str]] = []

        for py_file in py_files:
            file_inventory = _extract_file_inventory(py_file, workspace_root)
            modules.append(
                {
                    "path": file_inventory["path"],
                    "docstring": file_inventory["module_docstring"],
                }
            )
            for cls in file_inventory["classes"]:
                classes.append(
                    {
                        "path": file_inventory["path"],
                        **cls,
                    }
                )
            for fn in file_inventory["functions"]:
                functions.append(
                    {
                        "path": file_inventory["path"],
                        **fn,
                    }
                )
            for import_stmt in file_inventory["imports"]:
                imports.append({"path": file_inventory["path"], "import": import_stmt})

        subsystem_name = target_dir.name
        arch_doc = (workspace_root / "docs" / "architecture" / f"{subsystem_name}.md").resolve()
        existing_doc = ""
        if arch_doc.exists() and arch_doc.is_file():
            existing_doc = arch_doc.read_text(encoding="utf-8")

        result: dict[str, Any] = {
            "subsystem_path": target_dir.as_posix(),
            "subsystem_name": subsystem_name,
            "architecture_doc_path": _relative_path(arch_doc, workspace_root),
            "modules": modules,
            "classes": classes,
            "functions": functions,
            "imports": imports,
            "existing_doc": existing_doc,
        }

        latency_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "Step '%s': extracted inventory for %d files (%d classes, %d functions)",
            step.id,
            len(py_files),
            len(classes),
            len(functions),
        )
        return StepOutput(
            raw=json.dumps(result, indent=2),
            json=result,
            step_id=step.id,
            latency_ms=latency_ms,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        if "subsystem_path" not in inputs:
            errors.append(
                f"Step '{step.id}': doc_generate_extract_docstrings requires "
                "'subsystem_path' in handler_inputs"
            )
        return errors
