"""Subdivision-tree chunker for statutes, regulations, and probate code.

Per docs/architecture/entity-backed-claim-provenance.md § 3.2 / § 2.3:
statute / regulation / probate_code authorities chunk at leaf
subdivisions, with the pinpoint label = the dotted path to that leaf
(e.g. ``f-1-B`` for ``(f)(1)(B)``).

Conventional Cal. Rev. & Tax. Code / CCR / Probate Code numbering
nests at three depths in this order:

  Depth 1: lower-case letter        ``(a)`` ``(b)`` ``(c)``
  Depth 2: arabic digit             ``(1)`` ``(2)`` ``(3)``
  Depth 3: upper-case letter        ``(A)`` ``(B)`` ``(C)``
  (deeper nesting — roman numerals, ``(I)``, etc. — exists in some
   jurisdictions but is out of scope for v1; spec § 10.1 / § 10.8)

The chunker walks the input line-by-line, tracks current
``(letter, digit, upper)`` context, and emits one chunk per *leaf*
node — a node whose own text body is non-empty and which has no
descendants that themselves carry text. Preamble text before the first
``(a)`` is emitted as a single ``preamble`` chunk so nothing in the
source is silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .chunk_spec import ChunkSpec

_DEPTH1_RE = re.compile(r"^\(([a-z])\)\s*(.*)$")
_DEPTH2_RE = re.compile(r"^\((\d{1,2})\)\s*(.*)$")
_DEPTH3_RE = re.compile(r"^\(([A-Z])\)\s*(.*)$")


@dataclass(slots=True)
class _Node:
    """One node in the subdivision tree under construction."""

    label: str  # e.g. "a", "1", "B"; "" for the synthetic root
    body_lines: list[str] = field(default_factory=list)
    children: list["_Node"] = field(default_factory=list)


def _parse_marker(line: str) -> tuple[int, str, str] | None:
    """Return (depth, label, body_after_marker) for a subdivision marker.

    Depth 1=letter, 2=digit, 3=upper-letter. Returns None if *line* does
    not begin with a marker.
    """
    stripped = line.lstrip()
    m = _DEPTH3_RE.match(stripped)
    if m:
        return (3, m.group(1), m.group(2).strip())
    m = _DEPTH2_RE.match(stripped)
    if m:
        return (2, m.group(1), m.group(2).strip())
    m = _DEPTH1_RE.match(stripped)
    if m:
        return (1, m.group(1), m.group(2).strip())
    return None


def _build_tree(text: str) -> tuple[list[str], _Node]:
    """Parse *text* into a (preamble_lines, root_node) pair.

    Lines before the first depth-1 marker accumulate into ``preamble_lines``.
    A ``stack`` indexed [0..3] tracks the ancestor at each depth — depth 0
    is the synthetic root. Encountering a marker at depth d truncates the
    stack to length d, attaches a new child to ``stack[d-1]``, and pushes
    the new node onto the stack.
    """
    root = _Node(label="")
    stack: list[_Node] = [root]
    preamble: list[str] = []
    saw_marker = False

    for line in text.splitlines():
        parsed = _parse_marker(line)
        if parsed is None:
            if saw_marker:
                stack[-1].body_lines.append(line)
            else:
                preamble.append(line)
            continue
        depth, label, body_after = parsed
        saw_marker = True
        if depth > len(stack):
            depth = len(stack) + 1 if depth - 1 == len(stack) - 1 else depth
            depth = min(depth, len(stack) + 1)
        del stack[depth:]
        node = _Node(label=label)
        if body_after:
            node.body_lines.append(body_after)
        stack[-1].children.append(node)
        stack.append(node)

    return preamble, root


def _collect_leaves(
    node: _Node, path: list[str], leaves: list[tuple[str, str]]
) -> None:
    """Walk the tree and append (pinpoint, text) for each leaf node.

    A leaf is a node with no children. Internal nodes whose own
    ``body_lines`` carry text (e.g. an introductory clause for a list
    of subparagraphs) are also emitted, with their text stripped of
    descendants' content. This preserves the contract that every
    structurally-distinct unit of the source is addressable.
    """
    pinpoint = "-".join(path) if path else ""
    own_text = "\n".join(line for line in node.body_lines if line.strip())
    if not node.children:
        if own_text:
            leaves.append((pinpoint, own_text))
        return

    if own_text:
        leaves.append((pinpoint, own_text))

    for child in node.children:
        _collect_leaves(child, [*path, child.label], leaves)


def chunk_subdivision_tree(text: str) -> list[ChunkSpec]:
    """Chunk statutory text into one chunk per leaf subdivision.

    Behavior:
      * Text before any ``(a)`` becomes a single chunk with pinpoint
        ``"preamble"`` — preserves source coverage.
      * Each leaf subdivision emits one chunk with dotted-path pinpoint
        (e.g. ``"f-1-B"`` for ``(f)(1)(B)``).
      * Internal nodes with their own body text emit a chunk at the
        intermediate path so introductory clauses are addressable.
      * Empty input emits a single empty chunk with no pinpoint —
        preserves the default chunker's no-loss invariant.
    """
    if not text.strip():
        return [ChunkSpec(text=text, pinpoint=None)]

    preamble_lines, root = _build_tree(text)
    leaves: list[tuple[str, str]] = []
    for child in root.children:
        _collect_leaves(child, [child.label], leaves)

    chunks: list[ChunkSpec] = []
    preamble_text = "\n".join(line for line in preamble_lines if line.strip())
    if preamble_text:
        chunks.append(ChunkSpec(text=preamble_text, pinpoint="preamble"))

    for pinpoint, leaf_text in leaves:
        chunks.append(ChunkSpec(text=leaf_text, pinpoint=pinpoint))

    if not chunks:
        chunks = [ChunkSpec(text=text, pinpoint=None)]
    return chunks
