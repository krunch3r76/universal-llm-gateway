"""Regression tests for ``dispatch_ops._shared._compute_content_hash``.

Pins the URI-scheme handling that resolves ``cortex://`` source_uris to a
filesystem path under ``CORTEX_FILES_ROOT``. The bug this test was authored
against: prior to the strip, every entity_update that passed
``source_uri="cortex://..."`` (the corpus convention for skill files under
``agent-skills/``) silently produced None and skipped the auto-recompute,
so skill bumps did not refresh ``entities.content_hash`` — undermining the
auditor-validatable-confidence chain that depends on hash freshness as
provenance evidence.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cortex_store.dispatch_ops import _shared


_TEXT = "# v3.3\n\nFresh skill body.\n"
_TEXT_SHA = "sha256:" + hashlib.sha256(_TEXT.encode("utf-8")).hexdigest()


@pytest.fixture()
def files_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand up an isolated ``_FILES_ROOT`` with one fixture file."""
    root = tmp_path / "files"
    (root / "agent-skills").mkdir(parents=True)
    (root / "agent-skills" / "foo.md").write_text(_TEXT, encoding="utf-8")
    monkeypatch.setattr(_shared, "_FILES_ROOT", root)
    return root


def test_bare_relative_path_resolves(files_root: Path) -> None:
    """The legacy call shape — bare relative path — still works."""
    assert _shared._compute_content_hash("agent-skills/foo.md") == _TEXT_SHA


def test_cortex_scheme_prefix_strips_and_resolves(files_root: Path) -> None:
    """The corpus-convention URI shape now produces a hash (was returning None).

    Prior to the fix this returned None because ``_FILES_ROOT / source_uri``
    treated the scheme as part of the filename and produced a path like
    ``<root>/cortex://agent-skills/foo.md`` that is_file() rejected.
    """
    assert (
        _shared._compute_content_hash("cortex://agent-skills/foo.md") == _TEXT_SHA
    )


def test_cortex_scheme_and_bare_path_yield_identical_hash(files_root: Path) -> None:
    """Equivalence: stripped URI form must produce the same hash as bare form."""
    bare = _shared._compute_content_hash("agent-skills/foo.md")
    scheme = _shared._compute_content_hash("cortex://agent-skills/foo.md")
    assert bare == scheme == _TEXT_SHA


def test_workspaces_scheme_returns_none(files_root: Path) -> None:
    """workspaces:// URIs resolve outside CORTEX_FILES_ROOT — None, not a stale hash."""
    assert (
        _shared._compute_content_hash(
            "workspaces://universal-llm-gateway/tmp/prompts/x/README.md"
        )
        is None
    )


def test_https_scheme_returns_none(files_root: Path) -> None:
    """https:// URIs are not file-resolvable here — None."""
    assert _shared._compute_content_hash("https://example.com/x.md") is None


def test_files_scheme_returns_none(files_root: Path) -> None:
    """files:// URIs require different resolution — None until that lands."""
    assert _shared._compute_content_hash("files:///etc/passwd") is None


def test_missing_file_returns_none(files_root: Path) -> None:
    """Path resolves but file does not exist — None."""
    assert (
        _shared._compute_content_hash("cortex://agent-skills/does-not-exist.md")
        is None
    )
    assert _shared._compute_content_hash("agent-skills/does-not-exist.md") is None
