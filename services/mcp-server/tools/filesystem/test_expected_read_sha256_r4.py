"""S0 r4 — repo-relative workspaces alias matching with thread=."""

from __future__ import annotations

from pathlib import Path

import pytest

from fs_impl import fs_impl
from lane_branch_root import GATEWAY_REPO_DIRNAME
from tools._hashing import sha256_hex_of_file
from tools.filesystem._expected_read_sha256_boundary import (
    accepted_key_spellings,
    coerce_expected_read_sha256_at_fs_boundary,
)
from tools.filesystem.test_expected_read_sha256_boundary import (
    _fs_common,
    _md_registry,
    _patch_ws_read,
)

pytestmark = pytest.mark.offline
_REPO = GATEWAY_REPO_DIRNAME


def _bind_lane(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lane: Path,
    shared: Path,
) -> None:
    shared.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: shared)
    monkeypatch.setattr("tools.project._PROJECT_ROOT", lane)

    def _lane_root(_tid: str) -> Path:
        return lane

    monkeypatch.setattr("lane_branch_root.root_for_thread", _lane_root)
    monkeypatch.setattr(
        "lane_branch_root.branch_for_thread", lambda _t: "cursor-sdk/lane-23300"
    )
    _patch_ws_read(monkeypatch, lane)


def _lane_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rel: str,
    map_key: str,
    on_disk: Path | None = None,
) -> dict:
    shared = tmp_path / "shared"
    lane = tmp_path / "lane-wt"
    if on_disk is None:
        target = lane / rel
    else:
        target = on_disk
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Title\n\nlane-body\n", encoding="utf-8")
    live = sha256_hex_of_file(target)
    _bind_lane(monkeypatch, lane=lane, shared=shared)
    return fs_impl(
        **_fs_common(
            op="read",
            sandbox="workspaces",
            path=rel,
            thread="23300",
            section="Title",
            expected_read_sha256={map_key: live},
        )
    )


@pytest.mark.parametrize(
    "map_key",
    [
        f"workspaces://{_REPO}/docs/agent-guides/README.md",
        f"{_REPO}/docs/agent-guides/README.md",
        "docs/agent-guides/README.md",
    ],
)
def test_thread_repo_relative_accepted_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    map_key: str,
) -> None:
    rel = "docs/agent-guides/README.md"
    out = _lane_read(tmp_path, monkeypatch, rel=rel, map_key=map_key)
    assert out.get("code") != "invalid_parameter", out
    assert "lane-body" in out.get("content", "")


@pytest.mark.parametrize(
    "map_key",
    [
        "agent-guides/README.md",
        "workspaces://docs/agent-guides/README.md",
        f"workspaces://other-repo/docs/agent-guides/README.md",
    ],
)
def test_thread_repo_relative_rejected_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    map_key: str,
) -> None:
    rel = "docs/agent-guides/README.md"
    out = _lane_read(tmp_path, monkeypatch, rel=rel, map_key=map_key)
    assert out.get("code") == "invalid_parameter", out
    assert out.get("retryable") is True


def test_accepted_for_target_spellings_repo_relative() -> None:
    accepted = accepted_key_spellings(
        "docs/agent-guides/README.md",
        "workspaces",
        workspaces_target_repo=_REPO,
    )
    assert accepted == [
        "docs/agent-guides/README.md",
        f"{_REPO}/docs/agent-guides/README.md",
        f"workspaces://{_REPO}/docs/agent-guides/README.md",
    ]


@pytest.mark.parametrize(
    "map_key",
    [
        f"workspaces://{_REPO}/README.md",
        f"{_REPO}/README.md",
        "README.md",
    ],
)
def test_thread_root_readme_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    map_key: str,
) -> None:
    rel = "README.md"
    out = _lane_read(tmp_path, monkeypatch, rel=rel, map_key=map_key)
    assert out.get("code") != "invalid_parameter", out


@pytest.mark.parametrize(
    "map_key",
    [
        "workspaces://other-repo/README.md",
    ],
)
def test_thread_root_readme_other_repo_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    map_key: str,
) -> None:
    rel = "README.md"
    out = _lane_read(tmp_path, monkeypatch, rel=rel, map_key=map_key)
    assert out.get("code") == "invalid_parameter", out


def test_shared_workspaces_qualified_canonical_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repo-qualified shared path accepts canonical workspaces:// key."""
    projects = tmp_path / "projects"
    rel = f"{_REPO}/docs/agent-guides/README.md"
    (projects / rel).parent.mkdir(parents=True)
    (projects / rel).write_text("# Title\n\nshared\n", encoding="utf-8")
    live = sha256_hex_of_file(projects / rel)
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: projects)
    monkeypatch.setattr("tools.project._PROJECT_ROOT", projects)
    _patch_ws_read(monkeypatch, projects)
    out = fs_impl(
        **_fs_common(
            op="read",
            sandbox="workspaces",
            path=rel,
            section="Title",
            expected_read_sha256={f"workspaces://{rel}": live},
        )
    )
    assert out.get("code") != "invalid_parameter", out
    assert "shared" in out.get("content", "")


def test_map_value_empty_string_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = "docs/agent-guides/README.md"
    shared = tmp_path / "shared"
    lane = tmp_path / "lane-wt"
    target = lane / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Title\n\nlane-body\n", encoding="utf-8")
    _bind_lane(monkeypatch, lane=lane, shared=shared)
    out = fs_impl(
        **_fs_common(
            op="read",
            sandbox="workspaces",
            path=rel,
            thread="23300",
            section="Title",
            expected_read_sha256={rel: ""},
        )
    )
    assert out.get("code") == "invalid_parameter"


def test_map_value_non_string_rejected() -> None:
    _, err = coerce_expected_read_sha256_at_fs_boundary(
        op="read",
        expected={"notes/x.md": 123},
        path="notes/x.md",
        sandbox="cortex",
    )
    assert err is not None
    assert err.get("code") == "invalid_parameter"


def test_markdown_repo_relative_thread_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "shared"
    lane = tmp_path / "lane-wt"
    rel = "docs/agent-guides/README.md"
    target = lane / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nmd-body\n", encoding="utf-8")
    live = sha256_hex_of_file(target)
    _bind_lane(monkeypatch, lane=lane, shared=shared)
    md_fn = _md_registry(lane, monkeypatch)
    out = fs_impl(
        **_fs_common(
            overflow_registry={"markdown": md_fn},
            op="md_read",
            sandbox="workspaces",
            path=rel,
            thread="23300",
            section="Title",
            expected_read_sha256={rel: live},
        )
    )
    assert out.get("code") != "invalid_parameter", out
    assert "md-body" in out.get("content", "")
