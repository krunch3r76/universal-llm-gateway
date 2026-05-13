"""Default paragraph-boundary chunker — preserves pre-spec ingest behavior.

Splits text on blank-line paragraph boundaries and packs paragraphs into
chunks bounded by an approximate token budget. Used by ``chunk_for_authority``
when ``authority_class`` is ``None`` or unregistered.

Lifted out of ``libs/cortex_store/routes/ingest.py`` so authority-class
dispatch (spec § 3.2) is the single chunking surface in the codebase.
"""

from __future__ import annotations

import re

from .chunk_spec import ChunkSpec

_PARA_SPLIT = re.compile(r"\n{2,}")
_MAX_CHUNK_TOKENS = 800
_APPROX_CHARS_PER_TOKEN = 4


def chunk_default(text: str) -> list[ChunkSpec]:
    """Split *text* into paragraph-boundary chunks under a token budget.

    Chunks emitted by this default path carry no ``pinpoint`` — they are
    addressable by ``chunk_id`` only. The pinpoint slot is reserved for
    structurally-aware chunkers (subdivision-tree etc., spec § 3.2).
    """
    paragraphs = _PARA_SPLIT.split(text.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_tokens = len(para) // _APPROX_CHARS_PER_TOKEN
        if current and (current_len + para_tokens) > _MAX_CHUNK_TOKENS:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_tokens
        else:
            current.append(para)
            current_len += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    if not chunks:
        chunks = [text]
    return [ChunkSpec(text=chunk_text, pinpoint=None) for chunk_text in chunks]
