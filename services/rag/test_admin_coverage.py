from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_repo_root = str(Path(__file__).resolve().parents[2])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from services.rag.admin_routes._helpers import _coverage_sources  # noqa: E402


def test_coverage_sources_includes_cached_and_chroma_only_sources() -> None:
    prop_idx = MagicMock()
    prop_idx.get_sources.return_value = ["/docs/extracted.md"]
    prop_idx.get_indexed_sources.return_value = ["/poetry/chroma-only.md"]

    sources = _coverage_sources(
        prop_idx=prop_idx,
        chroma_sources={"/poetry/chroma-only.md", "/poetry/chroma-visible.md"},
    )

    assert sources == [
        "/docs/extracted.md",
        "/poetry/chroma-only.md",
        "/poetry/chroma-visible.md",
    ]
