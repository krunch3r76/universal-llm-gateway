import re
from hashlib import sha256
from typing import cast

import tree_sitter as _ts

MetadataValue = str | int | float | bool
_IMPORT_SYMBOL_RE: re.Pattern[str] = re.compile(r"[A-Za-z_][A-Za-z0-9_\.]*")
_IMPORT_SYMBOLS_CHAR_LIMIT = 800


def _node_text(source: bytes, node: _ts.Node) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _node_identifier(source: bytes, node: _ts.Node) -> str | None:
    target = node
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in {"function_definition", "class_definition"}:
                target = child
                break
        else:
            return None
    for child in target.children:
        if child.type == "identifier":
            return _node_text(source, child)
    return None


def chunk_names_from_nodes(
    source: bytes,
    nodes: list[_ts.Node],
) -> tuple[str | None, str | None]:
    """Detect (function_name, class_name) from nodes within a chunk."""
    for node in nodes:
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type == "class_definition":
                    return None, _node_identifier(source, node)
            return _node_identifier(source, node), None
        if node.type == "function_definition":
            return _node_identifier(source, node), None
        if node.type == "class_definition":
            return None, _node_identifier(source, node)
    return None, None


def _is_import_node(node: _ts.Node) -> bool:
    return node.type in {"import_statement", "import_from_statement"}


def _chunk_type_and_entity(
    source: bytes,
    nodes: list[_ts.Node],
    class_scope: str | None,
) -> tuple[str, str | None]:
    function_name, class_name = chunk_names_from_nodes(source, nodes)
    if class_name:
        return "class", class_name
    if function_name:
        if class_scope:
            return "method", function_name
        return "function", function_name
    if nodes and all(_is_import_node(node) for node in nodes):
        return "import_block", _node_text(source, nodes[0])
    if class_scope:
        return "partial_member", class_scope
    return "statement_block", None


def _is_semantically_complete(nodes: list[_ts.Node], chunk_type: str) -> bool:
    if chunk_type == "partial_member":
        return False
    return not any(node.has_error or node.is_missing for node in nodes)


def _iter_nodes(node: _ts.Node) -> list[_ts.Node]:
    items = [node]
    for child in node.children:
        items.extend(_iter_nodes(child))
    return items


def _extract_import_symbols(source: bytes, nodes: list[_ts.Node]) -> str:
    symbols: list[str] = []
    seen: set[str] = set()
    for root in nodes:
        for node in _iter_nodes(root):
            if node.type not in {"import_statement", "import_from_statement"}:
                continue
            text = _node_text(source, node)
            matches = cast(list[str], _IMPORT_SYMBOL_RE.findall(text))
            for match in matches:
                if match in {"import", "from", "as"}:
                    continue
                if match not in seen:
                    seen.add(match)
                    symbols.append(match)
                if len(symbols) >= 12:
                    result = ",".join(symbols)
                    if len(result) > _IMPORT_SYMBOLS_CHAR_LIMIT:
                        return f"{result[:_IMPORT_SYMBOLS_CHAR_LIMIT]}..."
                    return result
    result = ",".join(symbols)
    if len(result) > _IMPORT_SYMBOLS_CHAR_LIMIT:
        return f"{result[:_IMPORT_SYMBOLS_CHAR_LIMIT]}..."
    return result


def _extract_docstring_signals(
    source: bytes, nodes: list[_ts.Node]
) -> tuple[bool, bool, bool]:
    for node in nodes:
        target = node
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type in {"function_definition", "class_definition"}:
                    target = child
                    break
        if target.type not in {"function_definition", "class_definition"}:
            continue
        body = target.child_by_field_name("body")
        if body is None or not body.children:
            continue
        first_stmt = body.children[0]
        if first_stmt.type != "expression_statement" or not first_stmt.children:
            continue
        first_expr = first_stmt.children[0]
        if first_expr.type != "string":
            continue
        doc = _node_text(source, first_expr).strip("\"' \n\t")
        lowered = doc.lower()
        has_params = "param" in lowered or "args" in lowered
        has_return = "return" in lowered or "returns" in lowered
        return True, has_params, has_return
    return False, False, False


def complexity_score(nodes: list[_ts.Node]) -> float:
    """Approximate chunk complexity in [0.0, 1.0] for reranking."""
    decision_nodes = {
        "if_statement",
        "for_statement",
        "while_statement",
        "try_statement",
        "except_clause",
        "with_statement",
        "match_statement",
    }
    decision_count = 0
    max_depth = 0
    stack: list[tuple[_ts.Node, int]] = [(node, 1) for node in nodes]
    while stack:
        node, depth = stack.pop()
        max_depth = max(max_depth, depth)
        if node.type in decision_nodes:
            decision_count += 1
        for child in node.children:
            stack.append((child, depth + 1))
    normalized = (decision_count * 2 + max_depth) / 30.0
    return round(min(1.0, normalized), 4)


def build_python_chunk_metadata(
    *,
    path: str,
    source: bytes,
    text: str,
    nodes: list[_ts.Node],
    class_scope: str | None,
    nws_len: int,
) -> dict[str, MetadataValue]:
    """Build phase-2 Python metadata contract for one chunk."""
    chunk_type, entity_name = _chunk_type_and_entity(source, nodes, class_scope)
    start_line = nodes[0].start_point[0] + 1
    end_line = nodes[-1].end_point[0] + 1
    chunk_hash = sha256(text.encode()).hexdigest()[:16]
    function_name, class_name = chunk_names_from_nodes(source, nodes)
    effective_class = class_name or class_scope
    parent_entity = class_scope if chunk_type == "method" else None
    import_symbols = _extract_import_symbols(source, nodes)
    docstring_present, docstring_has_params, docstring_has_return = (
        _extract_docstring_signals(source, nodes)
    )

    metadata: dict[str, MetadataValue] = {
        "source": path,
        "language": "python",
        "chunk_type": chunk_type,
        "start_line": start_line,
        "end_line": end_line,
        "chunk_size_nws_chars": nws_len,
        "is_semantically_complete": _is_semantically_complete(nodes, chunk_type),
        "chunk_hash": chunk_hash,
        "docstring_present": docstring_present,
        "docstring_has_params": docstring_has_params,
        "docstring_has_return": docstring_has_return,
        "lightweight_complexity_score": complexity_score(nodes),
    }
    if entity_name:
        metadata["entity_name"] = entity_name
    if effective_class:
        metadata["class_name"] = effective_class
    if function_name:
        metadata["function_name"] = function_name
    if parent_entity:
        metadata["parent_entity"] = parent_entity
    if import_symbols:
        metadata["import_symbols"] = import_symbols
    return metadata
