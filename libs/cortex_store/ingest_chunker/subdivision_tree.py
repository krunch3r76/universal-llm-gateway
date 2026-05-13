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

**Out-of-scope inputs** — the chunker explicitly does NOT address these
and will surface a warning when it encounters them (rather than failing
silently):

  * Deeper nesting (lowercase / uppercase roman numerals like ``(i)``,
    ``(I)``, ``(iv)``) — the marker is logged as "marker-like body line"
    and treated as plain body text of the current depth-3 node. The
    pinpoint is lost; the text content is preserved.
  * Out-of-order depth (e.g. a depth-3 ``(B)`` before any depth-1/2
    ancestor exists) — the node is attached to the deepest available
    ancestor and a warning is logged with depth and label.
  * Duplicate sibling labels (e.g. two consecutive ``(a)`` siblings
    under the same parent) — both nodes are kept; ``_collect_leaves``
    emits a warning so the ambiguous pinpoint can be audited.

These warnings let ingest runs be audited for structural anomalies
without breaking ingestion.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .chunk_spec import ChunkSpec

logger = logging.getLogger("cortex-api.ingest_chunker.subdivision_tree")

_DEPTH1_RE = re.compile(r"^\(([a-z])\)\s*(.*)$")
_DEPTH2_RE = re.compile(r"^\((\d{1,2})\)\s*(.*)$")
_DEPTH3_RE = re.compile(r"^\(([A-Z])\)\s*(.*)$")

# Single-character lowercase-roman tokens that legally appear at depth-4
# in CA statutory numbering ((a)(1)(A)(i)). These collide with _DEPTH1_RE
# (single lowercase letter), so we explicitly exclude them — depth-4 is
# out of scope for v1 and gets logged as "marker-like body" by
# _MARKER_LIKE_RE instead.
_DEPTH4_ROMAN_TOKENS = frozenset({"i", "v", "x", "l", "c", "d", "m"})

# Broader marker-shape detector: anything that *looks* like a subdivision
# marker — single-paren-wrapped short alphanumeric label at line start —
# but isn't one of the three depth regexes. Used to surface silent drops
# of deeper-nesting markers (roman numerals, double-letter labels, etc.).
_MARKER_LIKE_RE = re.compile(r"^\s*\(([A-Za-z0-9]{1,4})\)")


@dataclass(slots=True)
class _Node:
    """One node in the subdivision tree under construction."""

    label: str  # e.g. "a", "1", "B"; "" for the synthetic root
    body_lines: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)


def _parse_marker(line: str) -> tuple[int, str, str] | None:
    """Return (depth, label, body_after_marker) for a subdivision marker.

    Depth 1=letter, 2=digit, 3=upper-letter. Returns None if *line* does
    not begin with a recognized marker. When the line LOOKS marker-shaped
    (matches ``_MARKER_LIKE_RE``) but isn't one of the three recognized
    depth patterns — e.g. roman numerals ``(i)``, ``(iv)`` — emit a debug
    log so ingest runs can be audited for silent depth-4+ drops.
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
    # Marker-shaped but not a recognized depth — surface for audit.
    if _MARKER_LIKE_RE.match(stripped):
        logger.debug(
            "subdivision_tree: marker-like line not matched by depth 1/2/3 "
            "regex — treating as body text: %r",
            stripped[:80],
        )
    return None


def _build_tree(text: str) -> tuple[list[str], _Node]:
    """Parse *text* into a (preamble_lines, root_node) pair.

    Lines before the first depth-1 marker accumulate into ``preamble_lines``.
    A ``stack`` indexed [0..3] tracks the ancestor at each depth — depth 0
    is the synthetic root. Encountering a marker at depth d truncates the
    stack to length d, attaches a new child to ``stack[d-1]``, and pushes
    the new node onto the stack.

    Out-of-order markers (e.g. a depth-3 ``(B)`` before any depth-1 ``(a)``)
    are clamped to one deeper than the current stack and a warning is
    logged. The clamp keeps ingestion robust; the warning keeps it auditable.
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
        # Context-aware depth-4 demotion: a depth-1-shaped marker whose
        # label is a roman-numeral token, appearing while the stack is
        # already at depth-3, is the real-world depth-4 case ((a)(1)(A)(i)).
        # v1 does not address depth-4 — keep the line as body text of the
        # current depth-3 node and log so audits can spot it.
        if (
            depth == 1
            and label in _DEPTH4_ROMAN_TOKENS
            and len(stack) >= 4
        ):
            logger.debug(
                "subdivision_tree: marker-like depth-4 roman %r under path %r — "
                "out of scope for v1, kept as body text",
                label,
                "-".join(n.label for n in stack[1:]),
            )
            stack[-1].body_lines.append(line)
            continue
        saw_marker = True
        if depth > len(stack):
            logger.warning(
                "subdivision_tree: out-of-order marker depth=%d label=%r "
                "with stack depth=%d — parent context missing, clamping",
                depth,
                label,
                len(stack),
            )
            depth = len(stack) + 1
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

    Duplicate sibling labels (rare but legal SQL for malformed input)
    are surfaced as a warning — both leaves still emit, but the resolver
    will return only one when keyed by ``(source_uri, pinpoint)``.
    """
    pinpoint = "-".join(path) if path else ""
    own_text = "\n".join(line for line in node.body_lines if line.strip())
    if not node.children:
        if own_text:
            leaves.append((pinpoint, own_text))
        return

    if own_text:
        leaves.append((pinpoint, own_text))

    seen_labels: set[str] = set()
    for child in node.children:
        if child.label in seen_labels:
            logger.warning(
                "subdivision_tree: duplicate sibling label %r under path %r "
                "— pinpoint collision; resolver will return only one chunk",
                child.label,
                "-".join(path) if path else "(root)",
            )
        seen_labels.add(child.label)
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

    Structural anomalies (out-of-order markers, duplicate sibling labels,
    deeper-than-depth-3 markers) are logged but do not abort ingestion.
    See module docstring for the full out-of-scope-input contract.
    """
    if not text.strip():
        return [ChunkSpec(text=text, pinpoint=None)]

    preamble_lines, root = _build_tree(text)
    leaves: list[tuple[str, str]] = []
    seen_root_labels: set[str] = set()
    for child in root.children:
        if child.label in seen_root_labels:
            logger.warning(
                "subdivision_tree: duplicate sibling label %r under path "
                "%r — pinpoint collision; resolver will return only one chunk",
                child.label,
                "(root)",
            )
        seen_root_labels.add(child.label)
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
