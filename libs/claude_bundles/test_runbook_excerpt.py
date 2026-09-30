"""ATX section extraction from runbook markdown."""

from __future__ import annotations

import pytest

from claude_bundles.runbook_excerpt import extract_sections

pytestmark = pytest.mark.offline

_SAMPLE = """\
# Title

## Trigger
Trigger body line.

## Refuse
Refuse body with contract=none rule.

## Steps
Step one.
"""


def test_extract_sections_returns_heading_and_body() -> None:
    out = extract_sections(_SAMPLE, ("Trigger", "Refuse"))
    assert "## Trigger" in out
    assert "Trigger body line." in out
    assert "## Refuse" in out
    assert "contract=none" in out


def test_extract_sections_missing_heading_raises() -> None:
    with pytest.raises(ValueError, match="missing heading"):
        extract_sections(_SAMPLE, ("Missing",))
