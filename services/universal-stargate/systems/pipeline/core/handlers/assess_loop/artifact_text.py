"""
Artifact text-shaping helpers for the assess_loop_v1 handler.

Pure functions with no handler/context dependency: they reshape artifact and
handler-input text before it reaches a prompt template or the stored artifact.
Lifted verbatim from the former ``assess_loop.py`` module level — behaviour is
byte-for-byte identical to the monolith.
"""

from __future__ import annotations

import re
from typing import Any


def _format_text_list(value: Any) -> Any:
    """Compress a list of dicts with a 'text' field into a numbered plain-text list.

    ∀ value: list[dict] ∧ "text" ∈ value[0] ⟹ "[1] text\n[2] text\n…"
    ∀ other value: returned unchanged.
    """
    if (
        isinstance(value, list)
        and value
        and isinstance(value[0], dict)
        and "text" in value[0]
    ):
        return "\n".join(
            f"[{i}] {item['text']}"
            for i, item in enumerate(value, 1)
            if item.get("text")
        )
    return value


def _pre_label_paragraphs(artifact: str) -> str:
    """Inject [P1], [P2], … labels before each paragraph block.

    Applied to the artifact in the assess prompt context only — the stored
    artifact and reviser input remain unlabeled so reviser output is clean.

    Invariant: paragraph boundaries = one or more blank lines between text blocks.
    """
    blocks = re.split(r"\n{2,}", artifact.strip())
    return "\n\n".join(f"[P{i}] {block.strip()}" for i, block in enumerate(blocks, 1))


def _strip_xml_tags(text: str, tags: list[str]) -> str:
    """Remove named XML blocks from text (e.g. <reasoning>...</reasoning>).

    ∀ tag ∈ tags: all occurrences stripped, including multiline content.
    Applied after each model response so downstream LLM calls and the final
    artifact are free of bookkeeping blocks the model appended for the assessor.
    """
    for tag in tags:
        text = re.sub(
            rf"<{re.escape(tag)}>.*?</{re.escape(tag)}>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return text.strip()
