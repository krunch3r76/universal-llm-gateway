"""Shared defaults, blocklists, and term-filter regex for corpus hints.

Imported by ``corpus_hints`` siblings ``cli``, ``update``, ``term_scoring``,
``loaders`` and ``cooccurrence``. Defines the ``prop.name@@`` / ``prop.topic@@``
key prefixes, per-prefix chunk-count bands for candidate terms, the metadata DB
path, a generic-term blocklist, and regexes rejecting structural noise such as
theorem labels, math variables and "et al" citations.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_KEY_PREFIXES = ["prop.name@@", "prop.topic@@"]
DEFAULT_MIN_CHUNKS_NAME = 2
DEFAULT_MAX_CHUNKS_NAME = 80
DEFAULT_MIN_CHUNKS_TOPIC = 3
DEFAULT_MAX_CHUNKS_TOPIC = 50
DEFAULT_METADATA_DB_PATH = Path.home() / ".rag" / "store" / "rag_metadata.db"
MIN_TERM_LENGTH = 3

GENERIC_BLOCKLIST: frozenset[str] = frozenset(
    {
        "datasets",
        "benchmarks",
        "models",
        "tools",
        "services",
        "arxiv",
        "institutions",
        "libraries",
        "stargate",
        "gateway",
        "gpt-4",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-3.5",
        "gpt-3.5-turbo",
        "bert",
        "openai",
        "chatgpt",
    }
)

DOCUMENT_STRUCTURE_RE = re.compile(
    r"^(theorem|lemma|figure|table|corollary|proposition|definition"
    r"|example|section|appendix|equation|proof|remark|claim)\s+[\d.a-z]",
    re.IGNORECASE,
)
MATH_VARIABLE_RE = re.compile(
    r"^[a-zA-Zα-ωΑ-Ω]\d*[\[\(].*[\]\)]$"
)
AUTHOR_CITATION_RE = re.compile(r"\bet\s+al\b\.?", re.IGNORECASE)
GREEK_SINGLE_RE = re.compile(r"^[α-ωΑ-Ω]$")
