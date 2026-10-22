"""Fact-token drift gate and filing-frame slices for outbound writing.

``run_drift_check`` in ``writing_intake.fact_inventory`` calls
``fact_token_flags`` so each load-bearing claim's content tokens must
survive in the draft. ``filing_frame`` copies caption, opener, and jurat
verbatim for the finalize stitch. Neither helper reads the network or
mutates its inputs.
"""

from __future__ import annotations

import re
from typing import Any

# Closed function-word list. ``not``, ``few``, ``stated``, ``said``, date
# words, and digits stay in the content set on purpose.
_FUNCTION_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "nor",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "over",
        "after",
        "before",
        "between",
        "about",
        "than",
        "then",
        "so",
        "yet",
        "is",
        "am",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "we",
        "you",
        "he",
        "she",
        "they",
        "me",
        "my",
        "our",
        "your",
        "his",
        "her",
        "their",
        "them",
        "him",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "would",
        "can",
        "could",
        "may",
        "might",
        "shall",
        "should",
        "just",
        "also",
        "very",
        "there",
        "here",
        "when",
        "where",
        "who",
        "whom",
        "which",
        "what",
        "up",
        "out",
        "off",
        "down",
        "no",
    }
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_OPENER_RE = re.compile(r"(?mi)^I,[^\n]+,\s+state:[ \t]*$")
_JURAT_RE = re.compile(r"(?mi)^I declare under penalty of perjury under the laws\b.*$")


def content_tokens(text: str) -> set[str]:
    """Return casefolded content tokens from ``text``.

    Tokens match ``[a-z0-9]+`` after casefold, minus the closed function-word
    list. ``not``, ``few``, ``stated``, ``said``, date words, and digits stay
    so a paraphrase that drops them is visible to the drift gate. No I/O.
    """
    return {
        token
        for token in _TOKEN_RE.findall((text or "").casefold())
        if token not in _FUNCTION_WORDS
    }


def fact_token_flags(
    draft_text: str,
    load_bearing_claims: list[str],
) -> list[dict[str, Any]]:
    """Flag each load-bearing claim whose content tokens are missing from the draft.

    A claim passes only when its content tokens are a subset of the draft
    tokens. Each miss records reason ``fact_inventory_drift`` and the missing
    tokens on ``flagged_sentences``. An empty claim list returns no rows.
    This does not read or write outside the two arguments.
    """
    draft_tokens = content_tokens(draft_text)
    flagged: list[dict[str, Any]] = []
    for claim in load_bearing_claims:
        text = str(claim or "").strip()
        if not text:
            continue
        missing = sorted(content_tokens(text) - draft_tokens)
        if not missing:
            continue
        flagged.append(
            {
                "sentence": text,
                "reason": "fact_inventory_drift",
                "missing_tokens": missing,
            }
        )
    return flagged


def filing_frame(prior_draft: str) -> dict[str, str]:
    """Copy caption, opener, and jurat slices out of a filing source.

    The opener is the ``I, ..., state:`` line. Caption is the verbatim prefix
    before that line. Jurat runs from the perjury sentence through the end of
    the source. Missing parts stay empty strings so callers do not invent a
    caption or jurat. The sworn body between opener and jurat is not returned.
    """
    text = prior_draft or ""
    opener_match = _OPENER_RE.search(text)
    opener = ""
    caption = ""
    search_from = 0
    if opener_match:
        opener = text[opener_match.start() : opener_match.end()].strip("\n")
        caption = text[: opener_match.start()].strip("\n")
        search_from = opener_match.end()
    jurat_match = _JURAT_RE.search(text, search_from)
    jurat = text[jurat_match.start() :].strip("\n") if jurat_match else ""
    return {"caption": caption, "opener": opener, "jurat": jurat}
