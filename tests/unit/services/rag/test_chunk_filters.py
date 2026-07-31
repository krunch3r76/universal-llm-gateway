"""Tests for services.rag.chunk_filters (bibliography/junk chunk detection)."""

from __future__ import annotations

from services.rag.chunk_filters import chunk_is_noise, is_citation_heavy


def test_pattern_a_numbered_footnote_lines_junk() -> None:
    """Pattern A: chunk with lines 'N `[url](https://...)' (numbered footnote style) → chunk_is_noise True."""
    # _JUNK_LINE_RE matches \d+\s+[`\[]https?:// so we need digit, space, then ` or [ then http
    lines = [f"{i} [https://example.com/ref/{i}]" for i in range(1, 11)]
    content = "\n".join(lines)
    assert chunk_is_noise(content) is True


def test_pattern_b_url_dense_markdown_links_junk() -> None:
    """Pattern B: chunk with 10 markdown link-only lines → chunk_is_noise True."""
    lines = [
        "[foo](https://example.com/1)",
        "[bar](https://example.com/2)",
        "[baz](https://example.com/3)",
    ] * 4  # 12 lines, all link-only
    content = "\n".join(lines[:10])
    assert chunk_is_noise(content) is True


def test_mixed_url_and_substantive_not_junk() -> None:
    """Mixed: 3 URL/link lines + 15 substantive lines → chunk_is_noise False."""
    url_lines = [
        "1 https://example.com/a",
        "[x](https://example.com/b)",
        "https://example.com/c",
    ]
    substantive = [
        "The main finding of this study is that the effect size was significant.",
        "We observed a strong correlation between the variables.",
    ] * 8  # 16 lines
    content = "\n".join(url_lines + substantive[:15])
    assert chunk_is_noise(content) is False


def test_citation_heavy_junk() -> None:
    """Citation-heavy content (author, year; et al.) → chunk_is_noise True."""
    lines = [
        "Smith, J., Jones, A., et al., 2020. Title of the paper.",
        "Brown, B., 2019. Another reference.",
    ] * 6  # 12 lines, all citation-like
    content = "\n".join(lines)
    assert is_citation_heavy(content, 0.25) is True
    assert chunk_is_noise(content) is True


def test_existing_junk_line_patterns() -> None:
    """Existing _JUNK_LINE_RE patterns (References, Bibliography, Table N) → chunk_is_noise True."""
    content = "References\n" + "\n".join(f"Table {i}" for i in range(1, 11))
    assert chunk_is_noise(content, threshold=0.35) is True


def test_empty_content_junk() -> None:
    """Empty or blank-only content → chunk_is_noise True."""
    assert chunk_is_noise("") is True
    assert chunk_is_noise("   \n\n  ") is True


def test_substantive_content_not_junk() -> None:
    """Normal prose → chunk_is_noise False."""
    content = """
    The methodology section describes the experimental setup.
    We used a double-blind design with 100 participants.
    Results indicate a significant main effect (p < 0.05).
    """
    assert chunk_is_noise(content) is False
