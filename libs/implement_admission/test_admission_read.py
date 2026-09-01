"""Tests for the front-matter readers in ``admission_read.py``.

``frontmatter_list_value`` is the block-list counterpart to
``frontmatter_value`` (scalar-only) — added for friction a:31774, where the
packet-authored ``files_expected:`` YAML list was silently ignored by the
scope-derivation scraper.
"""

from __future__ import annotations

from implement_admission.admission_read import frontmatter_list_value


def test_frontmatter_list_value_reads_block_list() -> None:
    text = """---
contract: implement
files_expected:
  - services/mcp-server/tools/filesystem/_fs_dispatch.py
  - services/mcp-server/fs_roots.py
---

<scope>
Body text.
</scope>
"""
    assert frontmatter_list_value(text, "files_expected") == [
        "services/mcp-server/tools/filesystem/_fs_dispatch.py",
        "services/mcp-server/fs_roots.py",
    ]


def test_frontmatter_list_value_absent_key_returns_none() -> None:
    text = """---
contract: implement
---

<scope>
Body text.
</scope>
"""
    assert frontmatter_list_value(text, "files_expected") is None


def test_frontmatter_list_value_present_but_empty_returns_empty_list() -> None:
    text = """---
contract: implement
files_expected:
---

<scope>
Body text.
</scope>
"""
    assert frontmatter_list_value(text, "files_expected") == []


def test_frontmatter_list_value_no_frontmatter_returns_none() -> None:
    text = "<scope>\nNo frontmatter at all.\n</scope>\n"
    assert frontmatter_list_value(text, "files_expected") is None


def test_frontmatter_list_value_scalar_key_returns_none() -> None:
    """A scalar value on the same line (not a block list) is not this shape."""
    text = """---
files_expected: single-file.py
---
"""
    assert frontmatter_list_value(text, "files_expected") is None


def test_frontmatter_list_value_stops_at_dedent() -> None:
    text = """---
files_expected:
  - a/b.py
  - c/d.py
contract: implement
---
"""
    assert frontmatter_list_value(text, "files_expected") == ["a/b.py", "c/d.py"]
