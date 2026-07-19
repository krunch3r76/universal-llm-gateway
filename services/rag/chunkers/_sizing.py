"""Chunk sizing constants and shared parsers."""

from __future__ import annotations

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
_PY_PARSER = _ts.Parser(_PY_LANG)
_AST_CHUNK_NWS_CHARS = _CHUNK_CHARS_CODE
_LOG = get_logger(__name__)
