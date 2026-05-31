"""
Tree-sitter extraction of Python code inventory.

Pure functions — no pipeline, no event bus, no async dependencies.
Consumers: doc-generate pipeline handler, /consult-review agent step,
scripts/docstring-quality, scripts/doc-check.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import tree_sitter as _ts
import tree_sitter_python as _tspython

_PY_LANG: _ts.Language = _ts.Language(
    _tspython.language()
)  # Tree-sitter language object for Python.
_PY_PARSER = _ts.Parser(_PY_LANG)  # Tree-sitter parser instance for Python.
_STRING_NODE_TYPES = {
    "string",
    "concatenated_string",
}  # Tree-sitter node types representing string literals.
_DEF_NODE_TYPES = {
    "function_definition",
    "async_function_definition",
}  # Tree-sitter node types for function definitions.


def _decode(node: _ts.Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _string_node_to_text(node: _ts.Node, source: bytes) -> str:
    literal = _decode(node, source)
    try:
        value: object = ast.literal_eval(literal)
    except (SyntaxError, ValueError):
        return literal.strip()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value).strip()


def _extract_docstring_from_block(block_node: _ts.Node | None, source: bytes) -> str:
    if block_node is None or not block_node.children:
        return ""
    first_child = block_node.children[0]
    if first_child.type != "expression_statement" or not first_child.children:
        return ""
    maybe_string = first_child.children[0]
    if maybe_string.type in _STRING_NODE_TYPES:
        return _string_node_to_text(maybe_string, source)
    return ""


def _signature(node: _ts.Node, source: bytes) -> str:
    body = node.child_by_field_name("body")
    end_byte = body.start_byte if body is not None else node.end_byte
    text = source[node.start_byte : end_byte].decode("utf-8", errors="replace").rstrip()
    text = text.removesuffix(":")
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


def extract_file_inventory(py_file: Path, workspace_root: Path) -> dict[str, Any]:
    """Extract docstring/signature inventory for a single Python file."""
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
        elif child.type in _DEF_NODE_TYPES:
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


def extract_subsystem_inventory(
    target_dir: Path, workspace_root: Path
) -> dict[str, Any]:
    """
    Extract full inventory for a subsystem directory.

    Returns dict with keys: subsystem_path, subsystem_name, architecture_doc_path,
    modules, classes, functions, imports, existing_doc.
    """
    target_dir = target_dir.resolve()
    py_files = sorted(target_dir.rglob("*.py"))

    modules: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    imports: list[dict[str, str]] = []

    for py_file in py_files:
        file_inv = extract_file_inventory(py_file, workspace_root)
        modules.append(
            {
                "path": file_inv["path"],
                "docstring": file_inv["module_docstring"],
            }
        )
        classes.extend(
            [{"path": file_inv["path"], **cls} for cls in file_inv["classes"]]
        )
        functions.extend(
            [{"path": file_inv["path"], **fn} for fn in file_inv["functions"]]
        )
        imports.extend(
            [
                {"path": file_inv["path"], "import": import_stmt}
                for import_stmt in file_inv["imports"]
            ]
        )

    subsystem_name = target_dir.name
    arch_doc = (
        workspace_root / "docs" / "architecture" / f"{subsystem_name}.md"
    ).resolve()
    existing_doc = ""
    arch_doc_rel = _relative_path(arch_doc, workspace_root)
    if arch_doc.exists() and arch_doc.is_file():
        existing_doc = arch_doc.read_text(encoding="utf-8")

    return {
        "subsystem_path": target_dir.as_posix(),
        "subsystem_name": subsystem_name,
        "architecture_doc_path": arch_doc_rel,
        "file_count": len(py_files),
        "modules": modules,
        "classes": classes,
        "functions": functions,
        "imports": imports,
        "existing_doc": existing_doc,
    }
