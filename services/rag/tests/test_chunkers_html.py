"""Unit tests for HTML normalization and chunking (RAG)."""

from pathlib import Path

import pytest

from services.rag.chunkers import chunk_file, normalize_html_to_markdown

_FIXTURE_PATH = Path("services/rag/tests/fixtures/sample_article_with_noise.html")


def test_html_normalization_preserves_core_structure() -> None:
    html = _FIXTURE_PATH.read_text()
    md = normalize_html_to_markdown("sample.html", html)
    assert "# Neural Retrieval Notes" in md
    assert "- Preserve links [paper](https://example.com/paper)" in md
    assert "> Ground answers in evidence." in md
    assert "| Metric | Value |" in md


def test_html_normalization_removes_boilerplate() -> None:
    html = _FIXTURE_PATH.read_text()
    md = normalize_html_to_markdown("sample.html", html)
    assert "We use cookies" not in md
    assert "Home | Docs | Pricing" not in md
    assert "copyright 2026" not in md


def test_html_normalization_is_deterministic() -> None:
    html = _FIXTURE_PATH.read_text()
    a = normalize_html_to_markdown("sample.html", html)
    b = normalize_html_to_markdown("sample.html", html)
    assert a == b


def test_chunk_file_html_adds_provenance_metadata(tmp_path: Path) -> None:
    fixture = Path("services/rag/tests/fixtures/sample_article_with_noise.html")
    html_path = tmp_path / "doc.html"
    _ = html_path.write_text(fixture.read_text())
    chunks = chunk_file(html_path)
    assert chunks
    assert all(chunk.metadata["source"] == str(html_path) for chunk in chunks)
    assert all(chunk.metadata["source_format"] == "html" for chunk in chunks)
    assert all(chunk.metadata["normalized_format"] == "markdown" for chunk in chunks)


def test_html_normalization_fails_on_effectively_empty_document() -> None:
    html = "<html><body><nav>menu</nav><footer>x</footer></body></html>"
    with pytest.raises(ValueError):
        _ = normalize_html_to_markdown("empty.html", html)
