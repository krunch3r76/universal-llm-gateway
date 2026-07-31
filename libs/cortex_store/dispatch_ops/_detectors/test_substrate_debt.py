"""Unit tests for substrate_debt_uri_fallback audit detector."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cortex_store.dispatch_ops._detectors.substrate_debt import (
    _scan_file,
    detect_substrate_debt_uri_fallback,
)
from cortex_store.dispatch_ops.ops_audit_detectors import (
    FS_TOUCHING_KINDS,
    GRAPH_ONLY_KINDS,
    INFO_KINDS,
    get_all_detectors,
)

_KIND = "substrate_debt_uri_fallback"


def _patch_scan_roots(
    monkeypatch: pytest.MonkeyPatch, files_root: Path
) -> None:
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._detectors.substrate_debt._FILES_ROOT",
        files_root,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._detectors.substrate_debt._SCAN_ROOTS",
        (
            files_root / "tasks" / "specs",
            files_root / "notes",
            files_root / "documents",
        ),
    )


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def test_kind_registered_in_fs_touching_and_registry() -> None:
    assert _KIND in FS_TOUCHING_KINDS
    assert _KIND not in GRAPH_ONLY_KINDS
    assert _KIND in get_all_detectors()


def test_dispositions_line_inside_section_yields_one_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files_root = tmp_path / "files"
    doc = files_root / "notes" / "system" / "artifact.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "# Draft\n\n"
        "## DISPOSITIONS\n"
        "C1: substrate_debt — uri_fallback — cortex://notes/foo.md — claim: backing spec\n",
        encoding="utf-8",
    )
    _patch_scan_roots(monkeypatch, files_root)

    findings = detect_substrate_debt_uri_fallback(_conn())
    assert len(findings) == 1
    assert findings[0]["kind"] == _KIND
    assert findings[0]["subject"] == "notes/system/artifact.md:4"
    assert "cortex://notes/foo.md" in findings[0]["detail"]
    assert "backing spec" in findings[0]["detail"]
    assert "notes/system/artifact.md:4" in findings[0]["detail"]


def test_dispositions_line_outside_section_yields_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files_root = tmp_path / "files"
    doc = files_root / "notes" / "system" / "artifact.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "C1: substrate_debt — uri_fallback — cortex://notes/foo.md — claim: backing spec\n",
        encoding="utf-8",
    )
    _patch_scan_roots(monkeypatch, files_root)

    findings = detect_substrate_debt_uri_fallback(_conn())
    assert len(findings) == 0


def test_standalone_substrate_debt_line_yields_one_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files_root = tmp_path / "files"
    doc = files_root / "notes" / "system" / "artifact.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "SUBSTRATE_DEBT: uri_fallback | workspaces://repo/path | claim summary\n",
        encoding="utf-8",
    )
    _patch_scan_roots(monkeypatch, files_root)

    findings = detect_substrate_debt_uri_fallback(_conn())
    assert len(findings) == 1
    assert findings[0]["subject"] == "notes/system/artifact.md:1"
    assert "workspaces://repo/path" in findings[0]["detail"]
    assert "claim summary" in findings[0]["detail"]


@pytest.mark.parametrize(
    "line",
    [
        "C1: substrate_debt — uri_fallback — cortex://x — claim:",
        "C1: substrate_debt — uri_fallback —  — claim: missing uri",
        "C1: substrate_debt — wrong_kind — cortex://x — claim: summary",
        "C1: substrate_debt — uri_fallback — cortex://x",
        "SUBSTRATE_DEBT: uri_fallback | | summary",
        "SUBSTRATE_DEBT: wrong | cortex://x | summary",
        "SUBSTRATE_DEBT: uri_fallback | cortex://x",
    ],
)
def test_malformed_lines_yield_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    files_root = tmp_path / "files"
    doc = files_root / "notes" / "system" / "artifact.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(f"## DISPOSITIONS\n{line}\n", encoding="utf-8")
    _patch_scan_roots(monkeypatch, files_root)

    findings = detect_substrate_debt_uri_fallback(_conn())
    assert len(findings) == 0


def test_dispositions_section_ends_at_next_heading(tmp_path: Path) -> None:
    doc = tmp_path / "artifact.md"
    doc.write_text(
        "## DISPOSITIONS\n"
        "C1: substrate_debt — uri_fallback — cortex://in — claim: inside\n"
        "## NEXT\n"
        "C2: substrate_debt — uri_fallback — cortex://out — claim: outside\n",
        encoding="utf-8",
    )
    hits = _scan_file(doc)
    assert len(hits) == 1
    assert hits[0][1] == "cortex://in"


def test_graph_only_session_audit_skips_detector() -> None:
    graph_default = list(GRAPH_ONLY_KINDS) + list(INFO_KINDS)
    assert _KIND not in graph_default

    with_fs = graph_default + list(FS_TOUCHING_KINDS)
    assert _KIND in with_fs


def test_dispositions_line_with_suggested_entity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files_root = tmp_path / "files"
    doc = files_root / "notes" / "system" / "artifact.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "## DISPOSITIONS\n"
        "C1: substrate_debt — uri_fallback — cortex://x — claim: y — suggested_entity: doc:foo\n",
        encoding="utf-8",
    )
    _patch_scan_roots(monkeypatch, files_root)

    findings = detect_substrate_debt_uri_fallback(_conn())
    assert len(findings) == 1
