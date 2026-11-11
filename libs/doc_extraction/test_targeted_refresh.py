"""Smoke tests for libs/doc_extraction/targeted_refresh.py."""

from __future__ import annotations

from pathlib import Path

from doc_extraction.targeted_refresh import main, refresh_routing

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _scope_section(text: str) -> str:
    start = text.index("## Scope of this pass")
    chunk = text[start:]
    end = chunk.index("\n---")
    return chunk[:end]


def test_main_usage_returns_2() -> None:
    assert main([]) == 2
    assert main(["routing"]) == 2
    assert main(["gateway", str(_REPO_ROOT), "extra"]) == 2


def test_refresh_routing_stamp_and_scope_trim() -> None:
    text = refresh_routing(_REPO_ROOT)
    pre_key = text.split("## Key Functions", 1)[0]
    assert "method=targeted-refresh" in pre_key
    scope = _scope_section(pre_key)
    assert "Step 9 subset" not in scope
    assert "Key Functions" in scope
    assert "full production routing tree" in scope
