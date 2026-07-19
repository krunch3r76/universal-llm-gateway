"""Paragraph splitting utilities and Chunk type."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True, kw_only=True)
class Chunk:
    text: str
    metadata: dict[str, str | int | float | bool]


def _word_split(text: str, max_chars: int) -> list[str]:
    """Split text at word boundaries up to max_chars per piece."""
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        if current_len + len(word) + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(word)
        current_len += len(word) + 1
    if current:
        chunks.append(" ".join(current))
    # Hard-truncation last resort (no whitespace at all).
    if not chunks and text:
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
    return chunks


def _split_oversized(para: str, max_chars: int) -> list[str]:
    """Split a single paragraph that individually exceeds max_chars.

    Strategy (cascade):
    1. Table rows: split at \\n boundaries for pipe-table content,
       recursing into _word_split for any row that still exceeds max_chars.
    2. Word-boundary split for prose.
    3. Hard-truncation as final fallback.
    """
    if "|" in para and "\n" in para:
        rows = para.split("\n")
        sub_chunks: list[str] = []
        current_rows: list[str] = []
        current_len = 0
        for row in rows:
            if current_len + len(row) + 1 > max_chars and current_rows:
                sub_chunks.append("\n".join(current_rows))
                current_rows = []
                current_len = 0
            if len(row) > max_chars:
                # Single row too large — word-split it, flush first.
                # _word_split may hard-truncate a single word; table formatting can break.
                if current_rows:
                    sub_chunks.append("\n".join(current_rows))
                    current_rows = []
                    current_len = 0
                sub_chunks.extend(_word_split(row, max_chars))
            else:
                current_rows.append(row)
                current_len += len(row) + 1
        if current_rows:
            sub_chunks.append("\n".join(current_rows))
        return sub_chunks

    return _word_split(para, max_chars)


def _overlap_prefix_len(carry: list[str]) -> int:
    """Char count of carry paragraphs joined with '\\n\\n' (no trailing separator)."""
    if not carry:
        return 0
    return sum(len(p) for p in carry) + max(0, (len(carry) - 1) * 2)


def _paras_len(paras: list[str]) -> int:
    return sum(len(p) for p in paras) + max(0, (len(paras) - 1) * 2)


def _char_upto_line(lines: list[str], line_idx: int) -> int:
    """Char offset at the start of 0-indexed ``line_idx``."""
    if line_idx <= 0:
        return 0
    safe_idx = min(line_idx, len(lines))
    return sum(len(lines[k]) for k in range(safe_idx))


def _split_paragraphs(
    text: str,
    target_chars: int,
    pad_chars: int,
    overlap_paragraphs: int = 2,
) -> list[tuple[str, int]]:
    """Split text into chunks with soft target, pad zone, and paragraph overlap.

    Returns (chunk_text, overlap_prefix_len) pairs.
    - Below target: keep accumulating paragraphs
    - In pad zone (target..target+pad): emit at next paragraph boundary
    - Above target+pad: force split via _split_oversized (overlap_prefix_len=0)

    ∀ chunk after first: starts with last `overlap_paragraphs` paragraphs from
    the previous chunk. overlap_prefix_len = char count of that carried-forward
    text (0 for first chunk and oversized fallback chunks).
    """
    hard_max = target_chars + pad_chars
    paragraphs = re.split(r"\n{2,}", text.strip())
    results: list[tuple[str, int]] = []
    current: list[str] = []
    current_len = 0
    carry: list[str] = []  # overlap paragraphs carried from the previous chunk

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(para) > hard_max:
            if current:
                results.append(("\n\n".join(current), _overlap_prefix_len(carry)))
            for piece in _split_oversized(para, hard_max):
                results.append((piece, 0))
            current = []
            current_len = 0
            carry = []
            continue

        new_len = current_len + (2 if current else 0) + len(para)

        if new_len > target_chars and current:
            if new_len <= hard_max:
                # In pad zone — include this paragraph, then emit.
                current.append(para)
                results.append(("\n\n".join(current), _overlap_prefix_len(carry)))
                carry = (
                    current[-overlap_paragraphs:]
                    if len(current) > overlap_paragraphs
                    else list(current)
                )
                current = list(carry)
                current_len = _paras_len(current)
            else:
                # Past pad zone — emit without this paragraph, then start new window.
                results.append(("\n\n".join(current), _overlap_prefix_len(carry)))
                carry = (
                    current[-overlap_paragraphs:]
                    if len(current) > overlap_paragraphs
                    else list(current)
                )
                current = list(carry) + [para]
                current_len = _paras_len(current)
        else:
            current.append(para)
            current_len = new_len

    if current:
        results.append(("\n\n".join(current), _overlap_prefix_len(carry)))

    return results


def _annotate_chunk_indices(chunks: list[Chunk]) -> list[Chunk]:
    for index, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = index
    return chunks
