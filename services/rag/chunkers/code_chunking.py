"""Code AST and line-based chunking."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import tree_sitter as _ts

from services.rag.chunker_ast_metadata import build_python_chunk_metadata
from services.rag.chunkers._sizing import (
    _AST_CHUNK_NWS_CHARS,
    _CHUNK_CHARS_CODE,
    _PY_PARSER,
)
from services.rag.chunkers.paragraph_utils import Chunk, _annotate_chunk_indices


def _nws_len(text: bytes) -> int:
    """Non-whitespace byte count (cAST chunk-size metric)."""
    return (
        len(text)
        - text.count(b" ")
        - text.count(b"\t")
        - text.count(b"\n")
        - text.count(b"\r")
    )


def _node_nws(source: bytes, node: _ts.Node) -> int:
    return _nws_len(source[node.start_byte : node.end_byte])


def _node_identifier(source: bytes, node: _ts.Node) -> str | None:
    """Extract identifier name from a function/class/decorated definition."""
    target = node
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                target = child
                break
        else:
            return None
    for child in target.children:
        if child.type == "identifier":
            return source[child.start_byte : child.end_byte].decode("utf-8")
    return None


def _class_scope(source: bytes, node: _ts.Node) -> str | None:
    """Return class name if this node introduces a class scope for its children."""
    if node.type == "class_definition":
        return _node_identifier(source, node)
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type == "class_definition":
                return _node_identifier(source, child)
    return None


def _split_children(node: _ts.Node) -> list[_ts.Node]:
    """Choose semantic children when splitting oversized nodes."""
    if node.type in {"class_definition", "function_definition"}:
        body = node.child_by_field_name("body")
        if body is not None and body.children:
            return list(body.children)
    return list(node.children)


def _chunk_ast_nodes(
    nodes: list[_ts.Node],
    source: bytes,
    max_nws: int,
    class_name: str | None,
) -> list[tuple[list[_ts.Node], str | None]]:
    """cAST Algorithm 1: recursive split-merge on AST sibling nodes.

    Returns (node_list, class_name) pairs — each pair becomes one Chunk.
    """
    results: list[tuple[list[_ts.Node], str | None]] = []
    current: list[_ts.Node] = []
    current_size = 0

    for node in nodes:
        s = _node_nws(source, node)

        if current_size + s > max_nws:
            if current:
                results.append((current, class_name))
                current = []
                current_size = 0

            if s > max_nws:
                # Keep decorated definitions atomic so decorators are never detached.
                if node.type == "decorated_definition":
                    results.append(([node], class_name))
                    continue

                children = _split_children(node)
                if children:
                    child_class = _class_scope(source, node) or class_name
                    results.extend(
                        _chunk_ast_nodes(children, source, max_nws, child_class)
                    )
                else:
                    results.append(([node], class_name))
            else:
                current = [node]
                current_size = s
        else:
            current.append(node)
            current_size += s

    if current:
        results.append((current, class_name))

    return results


def chunk_code_ast(
    path: str,
    content: str,
    max_chunk_chars: int = _AST_CHUNK_NWS_CHARS,
) -> list[Chunk]:
    """AST-aware Python chunker using tree-sitter (cAST split-merge algorithm).

    Chunks align with function/class boundaries.  Size metric is non-whitespace
    character count per the cAST paper (optimal range: 2000–2500).
    """
    source = content.encode()
    tree = _PY_PARSER.parse(source)
    root = tree.root_node

    if _node_nws(source, root) <= max_chunk_chars:
        whole_text = content if content.strip() else ""
        if not whole_text:
            return []
        # Keep metadata generation consistent with multi-chunk path.
        meta = build_python_chunk_metadata(
            path=path,
            source=source,
            text=content,
            nodes=root.children,
            class_scope=None,
            nws_len=_nws_len(content.encode()),
        )
        return _annotate_chunk_indices([Chunk(text=content, metadata=meta)])

    raw = _chunk_ast_nodes(root.children, source, max_chunk_chars, None)
    chunks: list[Chunk] = []
    for nodes, ctx_class in raw:
        if not nodes:
            continue
        text = source[nodes[0].start_byte : nodes[-1].end_byte].decode(
            "utf-8", errors="replace"
        )
        if not text.strip():
            continue

        meta = build_python_chunk_metadata(
            path=path,
            source=source,
            text=text,
            nodes=nodes,
            class_scope=ctx_class,
            nws_len=_nws_len(text.encode()),
        )
        chunks.append(Chunk(text=text, metadata=meta))

    return _annotate_chunk_indices(chunks)


def chunk_code(
    path: str,
    content: str,
    max_chunk_chars: int | None = None,
) -> list[Chunk]:
    """Code chunker: AST-aware for Python, line-based fallback for others."""
    if Path(path).suffix.lower() == ".py":
        budget = max_chunk_chars if max_chunk_chars else _AST_CHUNK_NWS_CHARS
        return chunk_code_ast(path, content, max_chunk_chars=budget)

    suffix = Path(path).suffix.lstrip(".")
    language = suffix or "text"
    source = str(path)
    budget = max_chunk_chars or _CHUNK_CHARS_CODE
    chunks: list[Chunk] = []

    lines = content.splitlines()
    current: list[str] = []
    current_chars = 0

    def _append_current_chunk() -> None:
        nonlocal current, current_chars
        chunk_text = "\n".join(current)
        # Positional prefix ensures identical text at different positions in the
        # same source yields distinct chunk_hash values (Task 3.0 invariant).
        chunk_index = len(chunks)
        positional_material = f"{chunk_index}|{chunk_text}".encode()
        chunks.append(
            Chunk(
                text=chunk_text,
                metadata={
                    "source": source,
                    "language": language,
                    "chunk_type": "statement_block",
                    "chunk_size_nws_chars": _nws_len(chunk_text.encode()),
                    "is_semantically_complete": False,
                    "chunk_hash": sha256(positional_material).hexdigest()[:16],
                },
            )
        )
        current = []
        current_chars = 0

    for line in lines:
        current.append(line)
        current_chars += len(line)
        if current_chars >= budget:
            _append_current_chunk()

    if current:
        _append_current_chunk()

    return _annotate_chunk_indices(chunks)
