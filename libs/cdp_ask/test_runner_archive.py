"""Offline tests for CDP ask archive path identity (a:25137 / F2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import default_archive_path

pytestmark = pytest.mark.offline


def test_default_archive_path_scopes_new_asks_by_execution_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    req = SubmitProjectAskRequest(converse=True, no_project_uuid=True)
    path_a = default_archive_path(req, execution_id="a" * 32)
    path_b = default_archive_path(req, execution_id="b" * 32)
    assert path_a != path_b
    assert path_a.endswith("/cdp-ask-archive-new-aaaaaaaa.md")
    assert path_b.endswith("/cdp-ask-archive-new-bbbbbbbb.md")


def test_default_archive_path_honors_explicit_archive_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    explicit = str(root / "notes/system/threads/custom-archive.md")
    req = SubmitProjectAskRequest(
        converse=True,
        no_project_uuid=True,
        archive_path=explicit,
    )
    assert default_archive_path(req, execution_id="deadbeef" * 4) == explicit
