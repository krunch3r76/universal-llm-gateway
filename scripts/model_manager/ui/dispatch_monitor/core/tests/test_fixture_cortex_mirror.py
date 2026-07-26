"""Fixture mirror contract — repo bytes must match cortex replay-fixtures/."""

from __future__ import annotations

import hashlib
import os

from .conftest import FIXTURE_DIR, FIXTURE_NAMES

_MANIFEST = os.path.join(FIXTURE_DIR, "CORTEX_MIRROR.sha256")


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_entries() -> dict[str, str]:
    entries: dict[str, str] = {}
    with open(_MANIFEST, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            digest, name = line.split(None, 1)
            entries[name.strip()] = digest
    return entries


def test_fixture_manifest_covers_inventory() -> None:
    """Every shipped fixture has a cortex-mirror hash entry."""
    manifest = _manifest_entries()
    assert set(manifest) == set(FIXTURE_NAMES)


def test_repo_fixtures_match_cortex_mirror_manifest() -> None:
    """Repo fixture bytes match the pinned cortex mirror hashes (D3)."""
    manifest = _manifest_entries()
    for name in FIXTURE_NAMES:
        path = os.path.join(FIXTURE_DIR, name)
        assert _file_sha256(path) == manifest[name], name
