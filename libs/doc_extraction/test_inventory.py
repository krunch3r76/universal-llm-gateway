"""
Tests for libs/doc_extraction/inventory.py — body projection (include_bodies).

Regression coverage for the producer/consumer contract that docstring_enhance's
collect_inventory step depends on: extract_file_inventory / extract_subsystem_inventory
must accept include_bodies and attach a `body_source` field when True. The absence of
this param (a "slim inventory projection" refactor) silently broke the docstring_enhance
collect step with a TypeError; these tests pin the param + the body_source shape so the
mismatch cannot recur undetected.
"""

from __future__ import annotations

from pathlib import Path

from doc_extraction import (
    behavioral_claim_symbols,
    extract_file_inventory,
    extract_subsystem_inventory,
)
from doc_extraction.inventory import _BODY_MAX_CHARS, _truncate_body

_SAMPLE = '''\
"""Module docstring for the sample."""


def top_level(a: int) -> int:
    """Double the input."""
    result = a * 2
    return result


class Widget:
    """A sample widget."""

    def render(self, label: str) -> str:
        """Render the widget. This never returns an empty string."""
        prefix = "w:"
        return prefix + label
'''


def _write(tmp_path: Path, text: str = _SAMPLE) -> Path:
    py_file = tmp_path / "sample.py"
    py_file.write_text(text, encoding="utf-8")
    return py_file


# ---------------------------------------------------------------------------
# extract_file_inventory — include_bodies contract
# ---------------------------------------------------------------------------


def test_file_inventory_default_omits_body_source(tmp_path: Path) -> None:
    inv = extract_file_inventory(_write(tmp_path), tmp_path)
    assert inv["functions"][0]["name"] == "top_level"
    assert "body_source" not in inv["functions"][0]
    cls = inv["classes"][0]
    assert "body_source" not in cls
    assert "body_source" not in cls["methods"][0]


def test_file_inventory_include_bodies_attaches_body_source(tmp_path: Path) -> None:
    inv = extract_file_inventory(_write(tmp_path), tmp_path, include_bodies=True)
    fn = inv["functions"][0]
    assert "result = a * 2" in fn["body_source"]
    cls = inv["classes"][0]
    assert "body_source" in cls
    method = cls["methods"][0]
    assert method["name"] == "render"
    assert 'prefix = "w:"' in method["body_source"]


def test_truncate_body_marks_long_bodies() -> None:
    long_text = "\n".join(f"line_{i} = {i}" for i in range(2000))
    out = _truncate_body(long_text)
    assert len(out) <= _BODY_MAX_CHARS + len("\n# [truncated]")
    assert out.endswith("# [truncated]")


def test_truncate_body_short_unchanged() -> None:
    assert _truncate_body("short = 1") == "short = 1"


# ---------------------------------------------------------------------------
# extract_subsystem_inventory — include_bodies propagation
# ---------------------------------------------------------------------------


def test_subsystem_inventory_include_bodies_propagates(tmp_path: Path) -> None:
    _write(tmp_path)
    inv = extract_subsystem_inventory(tmp_path, tmp_path, include_bodies=True)
    fn = next(f for f in inv["functions"] if f["name"] == "top_level")
    assert "result = a * 2" in fn["body_source"]


def test_subsystem_inventory_default_omits_bodies(tmp_path: Path) -> None:
    _write(tmp_path)
    inv = extract_subsystem_inventory(tmp_path, tmp_path)
    assert all("body_source" not in f for f in inv["functions"])


# ---------------------------------------------------------------------------
# behavioral_claim_symbols — only fires with bodies present
# ---------------------------------------------------------------------------


def test_behavioral_claim_symbols_uses_body_source(tmp_path: Path) -> None:
    inv = extract_file_inventory(_write(tmp_path), tmp_path, include_bodies=True)
    symbols = behavioral_claim_symbols(inv)
    # Widget.render's docstring makes a behavioral claim ("never returns ...").
    names = {s["name"] for s in symbols}
    assert "Widget.render" in names


def test_behavioral_claim_symbols_empty_without_bodies(tmp_path: Path) -> None:
    inv = extract_file_inventory(_write(tmp_path), tmp_path)
    assert behavioral_claim_symbols(inv) == []
