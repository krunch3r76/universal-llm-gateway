"""Chunk sizing constants and shared parsers.

Imported by every sibling chunker (``markdown_pdf``, ``code_chunking``,
``epub_html``, ``office_dispatch``). Budgets are defined in tokens and converted
to characters at a fixed 4 chars/token estimate (prose target 4096 + 1024 pad,
code 1024). Also holds the code/HTML extension sets, HTML boilerplate CSS
selectors, and the thread-local tree-sitter Python parser.
"""

from __future__ import annotations

import threading

import tree_sitter as _ts
import tree_sitter_python as _tspython
from universal_logging import get_logger

_TOKEN_ESTIMATE = 4  # chars per token approximation

_CHUNK_TOKENS_TARGET = 1024
_CHUNK_TOKENS_PAD = 256
_CHUNK_TOKENS_CODE = 256
_CHUNK_TOKENS_EBOOK = 1024
_CHUNK_TOKENS_EBOOK_PAD = 256

_CHUNK_CHARS_TARGET = _CHUNK_TOKENS_TARGET * _TOKEN_ESTIMATE  # 4096
_CHUNK_CHARS_PAD = _CHUNK_TOKENS_PAD * _TOKEN_ESTIMATE  # 1024

# Absolute UTF-8 byte ceiling for every chunk this package writes.
# Code-default embedder is qwen3-embedding-8b-q8-0-4096 (context 4096).
# max_batch_tokens_for_model = int(4096 * N_CTX_HEADROOM 0.85) = 3481.
# Embed estimator CHARS_PER_TOKEN is 3, so one chunk that fits a single
# embed-batch item is 3481 * 3 = 10443 bytes. The live hub model suffix
# is -8192, which is a looser batch. Retrieval default top_k is 20
# (services/mcp-server/tools/rag.py): 20 * 10443 bytes is about 209KB,
# the blow-up a ~100KB chunk times top_k used to produce. The cap is
# per chunk. A chunk above it is a bug.
CHUNK_HARD_CAP_BYTES = 3481 * 3
_CHUNK_CHARS_CODE = _CHUNK_TOKENS_CODE * _TOKEN_ESTIMATE
_CHUNK_CHARS_EBOOK = _CHUNK_TOKENS_EBOOK * _TOKEN_ESTIMATE  # 4096
_CHUNK_CHARS_EBOOK_PAD = _CHUNK_TOKENS_EBOOK_PAD * _TOKEN_ESTIMATE  # 1024

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".yaml", ".toml"}

_HTML_EXTENSIONS = {".html", ".htm"}
# Semantic landmarks — always strip (nav/footer-only pages must fail empty-doc check).
_STRICT_BOILERPLATE_SELECTORS = "nav, header, footer, aside, [role='navigation']"
# Class/id substring selectors — 50% text guard avoids false-positive stripping
# when a content wrapper matches (e.g. class="parade-loop-sidebar").
_GUARDED_BOILERPLATE_SELECTORS = (
    "[aria-label*='cookie' i], [class*='cookie' i], [id*='cookie' i], "
    "[class*='consent' i], [id*='consent' i], "
    "[class*='banner' i], [id*='banner' i], "
    "[class*='sidebar' i], [id*='sidebar' i], "
    "[class*='advert' i], [id*='advert' i], [class*='ad-' i], [id*='ad-']"
)

_PY_LANG = _ts.Language(_tspython.language())
# tree-sitter Parser is not thread-safe. Index workers (DEFAULT_INDEX_WORKERS)
# call chunk_code_ast concurrently, so each thread owns its own Parser.
_PY_PARSER_LOCAL = threading.local()
_AST_CHUNK_NWS_CHARS = _CHUNK_CHARS_CODE


def py_parser() -> _ts.Parser:
    """Return the tree-sitter Python parser for the calling thread.

    A shared Parser corrupts or races when ``chunk_code_ast`` runs from the
    index worker pool. The first call on a thread builds the parser; later
    calls on that thread reuse it.
    """
    parser = getattr(_PY_PARSER_LOCAL, "parser", None)
    if parser is None:
        parser = _ts.Parser(_PY_LANG)
        _PY_PARSER_LOCAL.parser = parser
    return parser


_LOG = get_logger(__name__)
