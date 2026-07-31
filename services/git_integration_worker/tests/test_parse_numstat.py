"""Unit tests for ``_parse_numstat`` (C6, thread 1147)."""

from __future__ import annotations

from services.git_integration_worker.routes.integrate import _parse_numstat


def test_parse_numstat_happy_path() -> None:
    raw = "3\t1\tfeature.py\n2\t0\tdocs/readme.md\n"
    stat = _parse_numstat(raw)
    assert stat.files_changed == 2
    assert stat.insertions == 5
    assert stat.deletions == 1
    assert stat.files[0].path == "feature.py"
    assert stat.files[0].insertions == 3
    assert stat.files[0].deletions == 1
    assert stat.files[0].binary is False


def test_parse_numstat_binary_excluded_from_totals() -> None:
    raw = "1\t0\ttext.py\n-\t-\tbinary.dat\n"
    stat = _parse_numstat(raw)
    assert stat.files_changed == 2
    assert stat.insertions == 1
    assert stat.deletions == 0
    assert stat.files[1].binary is True
    assert stat.files[1].insertions == 0
    assert stat.files[1].deletions == 0


def test_parse_numstat_rename_arrow_preserved() -> None:
    raw = "0\t0\told.py => new.py\n"
    stat = _parse_numstat(raw)
    assert stat.files[0].path == "old.py => new.py"


def test_parse_numstat_embedded_tab_in_path() -> None:
    raw = "1\t0\tweird\tpath.py\n"
    stat = _parse_numstat(raw)
    assert stat.files[0].path == "weird\tpath.py"


def test_parse_numstat_skips_blank_and_malformed() -> None:
    raw = "\n   \n1\t0\tok.py\nno-tabs\n1\tonly-two\n"
    stat = _parse_numstat(raw)
    assert stat.files_changed == 1
    assert stat.files[0].path == "ok.py"


def test_parse_numstat_empty_input() -> None:
    stat = _parse_numstat("")
    assert stat.files_changed == 0
    assert stat.insertions == 0
    assert stat.deletions == 0
    assert stat.files == []
