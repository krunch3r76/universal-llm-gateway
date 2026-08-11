"""Unit tests for DIRECTIVE ``## options`` YAML block parsing."""

from __future__ import annotations

from implement_admission.options_block_parser import (
    options_block_present,
    parse_options_block,
    parse_options_yaml_document,
)


_SYMMETRIC_BLOCK = """\
## options

```yaml
options:
  - id: lean_path
    cost: low effort
    benefit: fast delivery
    falsifier: tests pass without refactor
  - id: full_path
    cost: higher effort
    benefit: durable structure
    falsifier: tests pass after modular split
```
"""


def test_parse_symmetric_options_block() -> None:
    rows, error, present = parse_options_block(_SYMMETRIC_BLOCK)
    assert present is True
    assert error is None
    assert len(rows) == 2
    assert rows[0][0] == "lean_path"
    assert rows[1][0] == "full_path"


def test_options_block_present_detects_heading_block() -> None:
    assert options_block_present(_SYMMETRIC_BLOCK)


def test_inline_options_without_heading() -> None:
    body = """\
TYPE: DIRECTIVE
options:
  - id: a
    cost: x
    benefit: y
    falsifier: z
"""
    rows, error, present = parse_options_block(body)
    assert present is True
    assert error is None
    assert rows[0][0] == "a"


def test_dict_shaped_options() -> None:
    yaml_text = """\
options:
  path_a:
    cost: low
    benefit: fast
    falsifier: smoke passes
  path_b:
    cost: high
    benefit: durable
    falsifier: integration passes
"""
    rows, error = parse_options_yaml_document(yaml_text)
    assert error is None
    assert {row[0] for row in rows} == {"path_a", "path_b"}


def test_unparseable_yaml_surfaces_error() -> None:
    body = """\
## options

```yaml
options:
  - id: broken
    cost: [unclosed
```
"""
    rows, error, present = parse_options_block(body)
    assert present is True
    assert rows == []
    assert error is not None
    assert "options_yaml_parse_error" in error
