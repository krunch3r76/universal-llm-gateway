"""Tests for L0 pages normalization."""

from __future__ import annotations

from pathlib import Path

import pytest
from continuity_tape.normalize_grokbot_l0_pages import (
    L0NormalizeError,
    normalize_grokbot_l0_pages,
    normalized_capture_ref,
)

pytestmark = pytest.mark.offline

_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = _ROOT / "pipelines/grokbot_checkpoint/v1/handlers/fixtures/bl1_drift.pages.txt"
_EXPECTED = _ROOT / "pipelines/grokbot_checkpoint/v1/handlers/fixtures/bl1_drift.normalized.pages.txt"
_SPECIMEN_RAW = Path(
    "/mnt/torus/mcp-data/files/notes/system/tape/captures/grok/"
    "85dcee4a-0b20-4a86-95ae-01a529464072/20261017T203227.pages.txt"
)
_SPECIMEN_NORM = _SPECIMEN_RAW.with_name("20261017T203227.normalized.pages.txt")


def test_wrapper_strip_and_header_alignment_fixture() -> None:
    raw = _FIXTURE.read_text()
    expected = _EXPECTED.read_bytes()
    norm, stats = normalize_grokbot_l0_pages(raw)
    assert norm.encode() == expected
    assert stats.wrapper_lines_stripped == 4
    assert stats.header_total_aligned == 99


def test_fake_wrapper_inside_role_line_preserved() -> None:
    raw = _FIXTURE.read_text()
    role_with_wrapper = (
        '{"role":"user","message":{"content":[{"type":"text","text":'
        '"<cursor_untrusted_data_1337 source=\\"ReadTranscript\\"> stays"}]}}'
    )
    raw = raw + "\n" + role_with_wrapper + "\n"
    norm, _ = normalize_grokbot_l0_pages(raw)
    assert role_with_wrapper in norm.splitlines()


@pytest.mark.skipif(not _SPECIMEN_RAW.is_file(), reason="gc1 specimen not on disk")
def test_specimen_byte_match() -> None:
    raw = _SPECIMEN_RAW.read_text()
    expected = _SPECIMEN_NORM.read_bytes()
    norm, stats = normalize_grokbot_l0_pages(raw)
    assert norm.encode() == expected
    assert stats.wrapper_lines_stripped == 18


def test_normalized_capture_ref() -> None:
    assert normalized_capture_ref(
        "cortex://notes/system/tape/captures/grok/x/y.pages.txt"
    ).endswith(".normalized.pages.txt")


def test_missing_header_refuses() -> None:
    with pytest.raises(L0NormalizeError, match="no ReadTranscript page header"):
        normalize_grokbot_l0_pages('{"role":"user","message":{}}\n')
