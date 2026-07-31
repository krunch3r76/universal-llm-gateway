"""Hermetic tests for contract-aware pinning and directory-target rejection (24298)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cortex_store.dispatch_ops import _pinned_deliverable as pinned_mod
from cortex_store.dispatch_ops._pinned_deliverable import write_pinned_deliverable_impl

from services.git_integration_worker.cursor_sdk_closeout import (
    _files_expected_for_pinning,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    resolve_cortex_pinned_deliverables,
)

pytestmark = pytest.mark.offline

_REFERENCE_ONLY_PACKET = """\
<scope>
Review the templates directory at `cortex://notes/system/templates/`.
</scope>
"""

_IMPLEMENT_PACKET = """\
<scope>
Modify:
- `cortex://notes/system/specs/my-spec.md`
- `libs/foo/bar.py`
</scope>
"""

_CONSULT_PACKET = """\
<scope>
Consult on `cortex://notes/system/templates/` usage patterns.
</scope>
"""


def test_files_expected_for_pinning_prefers_light_bounded_paths() -> None:
    light_bounded = ("cortex://notes/system/threads/5121-review.md",)
    result = _files_expected_for_pinning(
        _REFERENCE_ONLY_PACKET,
        deliverables_expected=False,
        light_bounded_expected_paths=light_bounded,
    )
    assert result == list(light_bounded)


def test_files_expected_for_pinning_retains_packet_paths_when_deliverables_expected() -> (
    None
):
    result = _files_expected_for_pinning(
        _IMPLEMENT_PACKET,
        deliverables_expected=True,
        light_bounded_expected_paths=(),
    )
    assert "cortex://notes/system/specs/my-spec.md" in result
    assert "libs/foo/bar.py" in result


def test_files_expected_for_pinning_empty_for_non_deliverable_consult() -> None:
    result = _files_expected_for_pinning(
        _CONSULT_PACKET,
        deliverables_expected=False,
        light_bounded_expected_paths=(),
    )
    assert result == []


def test_resolve_cortex_pinned_deliverables_rejects_directory_target(
    tmp_path: Path,
) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    rel = "notes/system/templates"
    (cortex_root / rel).mkdir(parents=True)

    writer = AsyncMock(return_value={"uri": "cortex://notes/system/templates"})

    resolution = asyncio.run(
        resolve_cortex_pinned_deliverables(
            files_expected=[f"cortex://{rel}"],
            full_text="closeout body",
            source_repo=source_repo,
            dispatch_id="dispatch-1",
            thread_id="thread-1",
            post_pinned=writer,
            cortex_root=cortex_root,
        )
    )

    writer.assert_not_called()
    assert resolution.divergent_rels == (
        "pinned_deliverable_invalid_target:notes/system/templates",
    )


def test_write_pinned_deliverable_impl_rejects_directory_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files_root = tmp_path / "cortex_files"
    files_root.mkdir()
    rel = "notes/system/templates"
    (files_root / rel).mkdir(parents=True)
    monkeypatch.setattr(pinned_mod, "_FILES_ROOT", files_root)

    result = write_pinned_deliverable_impl(
        f"cortex://{rel}",
        content="should not write",
        write_if_absent=True,
    )

    assert result == {"error": "rel_path resolves to a directory"}
