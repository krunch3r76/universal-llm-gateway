"""
Tree-sitter extraction of Python code inventory.

Pure functions — no pipeline, no event bus, no async dependencies.
Consumers: doc-generate pipeline handler, /consult-review agent step,
scripts/docstring-quality, scripts/doc-check.
"""

from __future__ import annotations

import ast
import re
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

_GOOGLE_ARG_HDR = re.compile(r"^\s*(Args|Arguments|Parameters)\s*:\s*$", re.MULTILINE)
_GOOGLE_ARG_LINE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:")
_SPHINX_PARAM = re.compile(r":param\s+(?:\S+\s+)?([A-Za-z_]\w*)\s*:")
_RETURN_HINT = re.compile(r"(?:^|\n)\s*(Returns?|:returns?:)", re.IGNORECASE)


def _sig_param_names(signature: str) -> set[str]:
    """Param names from a signature string via ast (handles nested-paren hints)."""
    stub = signature.strip()
    if not (stub.startswith("def ") or stub.startswith("async def ")):
        return set()
    try:
        mod = ast.parse(stub + ": ...")
    except SyntaxError:
        return set()
    fn = mod.body[0]
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    a = fn.args
    names: set[str] = set()
    for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
        names.add(arg.arg)
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    names.discard("self")
    names.discard("cls")
    return names


def _doc_param_mentions(docstring: str) -> set[str]:
    """Param names explicitly named in the docstring (Sphinx + Google styles)."""
    mentions: set[str] = set(_SPHINX_PARAM.findall(docstring))
    in_args = False
    for line in docstring.splitlines():
        if _GOOGLE_ARG_HDR.match(line):
            in_args = True
            continue
        if in_args:
            if not line.strip():
                in_args = False
                continue
            m = _GOOGLE_ARG_LINE.match(line)
            if m:
                mentions.add(m.group(1))
    mentions.discard("self")
    mentions.discard("cls")
    return mentions


def _divergence_issue(
    *, path: str, line: int, scope: str, name: str, issue: str, excerpt: str
) -> dict[str, Any]:
    return {
        "path": path,
        "line": line,
        "scope": scope,
        "name": name,
        "issue": issue,
        "severity": "warning",
        "words": 0,
        "threshold": 0,
        "excerpt": excerpt[:80],
    }


def _build_divergence_issues(
    path: str, symbol: dict[str, Any], scope: str
) -> list[dict[str, Any]]:
    signature = str(symbol.get("signature", ""))
    docstring = str(symbol.get("docstring", "")).strip()
    if not docstring:
        return []
    line = int(symbol.get("line", 1))
    name = str(symbol.get("name", ""))
    issues: list[dict[str, Any]] = []

    sig_params = _sig_param_names(signature)
    drifted = sorted(_doc_param_mentions(docstring) - sig_params)
    if drifted:
        issues.append(
            _divergence_issue(
                path=path, line=line, scope=scope, name=name,
                issue="param_drift",
                excerpt="doc params not in signature: " + ", ".join(drifted),
            )
        )

    no_return = ("->" not in signature) or ("-> None" in signature)
    if no_return and _RETURN_HINT.search(docstring):
        issues.append(
            _divergence_issue(
                path=path, line=line, scope=scope, name=name,
                issue="return_drift",
                excerpt="docstring documents a return; signature returns None/unannotated",
            )
        )
    return issues


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


_BODY_MAX_CHARS = 2000  # per-symbol body cap fed to docstring_enhance (line boundary)


def _truncate_body(text: str, max_chars: int = _BODY_MAX_CHARS) -> str:
    """Clip a decoded body to max_chars at a line boundary, marking truncation."""
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    newline = clipped.rfind("\n")
    if newline > 0:
        clipped = clipped[:newline]
    return clipped + "\n# [truncated]"


def _body_text(node: _ts.Node, source: bytes) -> str:
    """Decode the source body region of a definition node, bounded by _BODY_MAX_CHARS."""
    body = node.child_by_field_name("body")
    if body is None:
        return ""
    raw = source[body.start_byte : body.end_byte].decode("utf-8", errors="replace")
    return _truncate_body(raw)


def _extract_class_methods(
    class_node: _ts.Node, source: bytes, include_bodies: bool = False
) -> list[dict[str, Any]]:
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
        method: dict[str, Any] = {
            "name": _decode(method_name_node, source),
            "signature": _signature(member, source),
            "docstring": _extract_docstring_from_block(
                member.child_by_field_name("body"), source
            ),
            "line": member.start_point[0] + 1,
        }
        if include_bodies:
            method["body_source"] = _body_text(member, source)
        methods.append(method)
    return methods


def _relative_path(path: Path, anchor: Path) -> str:
    try:
        return path.relative_to(anchor).as_posix()
    except ValueError:
        return path.as_posix()


def extract_file_inventory(
    py_file: Path, workspace_root: Path, include_bodies: bool = False
) -> dict[str, Any]:
    """Extract docstring/signature inventory for a single Python file.

    When ``include_bodies`` is True, each function/method/class dict also carries a
    ``body_source`` field with the decoded source body. Default False preserves the
    projection-thesis corpus used by doc_generate (docstrings/signatures/imports only).
    """
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
            cls: dict[str, Any] = {
                "name": _decode(class_name_node, source),
                "signature": _signature(child, source),
                "docstring": _extract_docstring_from_block(
                    child.child_by_field_name("body"), source
                ),
                "line": child.start_point[0] + 1,
                "methods": _extract_class_methods(child, source, include_bodies),
            }
            if include_bodies:
                cls["body_source"] = _body_text(child, source)
            classes.append(cls)
        elif child.type in _DEF_NODE_TYPES:
            fn_name_node = child.child_by_field_name("name")
            if fn_name_node is None:
                continue
            fn: dict[str, Any] = {
                "name": _decode(fn_name_node, source),
                "signature": _signature(child, source),
                "docstring": _extract_docstring_from_block(
                    child.child_by_field_name("body"), source
                ),
                "line": child.start_point[0] + 1,
            }
            if include_bodies:
                fn["body_source"] = _body_text(child, source)
            functions.append(fn)

    rel_path = _relative_path(py_file, workspace_root)
    quality_issues: list[dict[str, Any]] = []
    for fn in functions:
        quality_issues.extend(_build_divergence_issues(rel_path, fn, "function"))
    for cls in classes:
        for method in cls.get("methods", []):
            quality_issues.extend(
                _build_divergence_issues(rel_path, method, "method")
            )

    return {
        "path": rel_path,
        "module_docstring": module_docstring,
        "imports": _extract_imports(module_node, source),
        "classes": classes,
        "functions": functions,
        "quality_issues": quality_issues,
    }


def extract_subsystem_inventory(
    target_dir: Path, workspace_root: Path, include_bodies: bool = False
) -> dict[str, Any]:
    """
    Extract full inventory for a subsystem directory.

    Returns dict with keys: subsystem_path, subsystem_name, architecture_doc_path,
    modules, classes, functions, imports, existing_doc. When ``include_bodies`` is
    True, class/function/method entries also carry decoded ``body_source`` text (used
    only by docstring_enhance; doc_generate never sets this).
    """
    target_dir = target_dir.resolve()
    py_files = sorted(target_dir.rglob("*.py"))

    modules: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    imports: list[dict[str, str]] = []

    for py_file in py_files:
        file_inv = extract_file_inventory(py_file, workspace_root, include_bodies)
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
