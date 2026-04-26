#!/usr/bin/env python3
"""CLI entry: validate and register third-party API doc mirrors with the RAG service.

Full behavior and module layout: ``thirdparty_mirror_ingest`` / ``thirdparty_mirror_walk``.

Usage:
    python scripts/rag/ingest-thirdparty-mirror.py --provider xai-api
    python scripts/rag/ingest-thirdparty-mirror.py --provider lighter --dry-run
    python scripts/rag/ingest-thirdparty-mirror.py --provider mcp --force-index
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_RAG = Path(__file__).resolve().parent
_WORKSPACE = _SCRIPTS_RAG.parents[1]
sys.path[:0] = (str(_WORKSPACE), str(_WORKSPACE / "libs"), str(_SCRIPTS_RAG))

from thirdparty_mirror_ingest import main  # noqa: E402

if __name__ == "__main__":
    main()
