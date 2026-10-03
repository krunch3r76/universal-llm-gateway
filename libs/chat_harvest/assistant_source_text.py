"""DOM source-text helper for claude.ai harvest evaluate scripts.

KaTeX turns ``$ULG_REPO: HOME="$(…`` into visual math glyphs. ``innerText`` of
that subtree is the rendered italics (a:37508). This function is injected into
harvest evaluate strings so a clone can swap ``.katex`` nodes for ``$tex$``
from the MathML annotation before ``innerText``.
"""

from __future__ import annotations

from pathlib import Path

ASSISTANT_SOURCE_TEXT_JS = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")


def with_assistant_source_text(evaluate_js: str) -> str:
    """Insert ``assistantSourceText`` after the evaluate arrow body's ``=> {``."""
    marker = "=> {"
    idx = evaluate_js.find(marker)
    if idx < 0:
        marker = "=>{"
        idx = evaluate_js.find(marker)
    if idx < 0:
        raise ValueError("evaluate_js has no arrow-function body")
    insert_at = idx + len(marker)
    return (
        evaluate_js[:insert_at]
        + "\n"
        + ASSISTANT_SOURCE_TEXT_JS
        + evaluate_js[insert_at:]
    )
