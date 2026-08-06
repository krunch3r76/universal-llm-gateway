"""Two-class write authority + episode collision replay falsifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.filesystem import _files_dispatcher as files_dispatcher
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _ops_write as ops_write
from tools.filesystem import _paths as paths
from tools.filesystem import _write_authority as authority


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    return root


def test_consult_unguarded_overwrite_refuses_with_digest(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/6655-demo-architecture-bind.md"
    first = ops_text.write_file_impl(rel, "opus-bind", if_absent=True)
    assert first["status"] == "written"
    rejected = ops_text.write_file_impl(rel, "fable-bind")
    assert rejected["reason"] == "consult_class.unguarded_overwrite"
    assert rejected["existing_sha256"].startswith("sha256:")
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "opus-bind"


def test_consult_if_absent_collision_installs_pointer(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/6655-demo-architecture-bind.md"
    ops_text.write_file_impl(rel, "peer-a", if_absent=True, author="opus")
    rejected = ops_text.write_file_impl(rel, "peer-b", if_absent=True, author="fable")
    assert rejected["reason"] == "file_exists"
    assert rejected.get("pointer_installed") is True
    body = (sandbox_root / rel).read_text(encoding="utf-8")
    assert "FORK POINTER" in body
    assert "peer-a" not in body
    assert rejected["existing_sha256"].startswith("sha256:")
    assert rejected["attempted_sha256"].startswith("sha256:")


def test_shared_write_requires_expected_sha256(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/specs/demo-roadmap.md"
    ops_text.write_file_impl(rel, "row-1\n", artifact_class="shared")
    rejected = ops_text.write_file_impl(rel, "row-1\nrow-2\n", artifact_class="shared")
    assert rejected["reason"] == "expected_sha256.required"
    current = paths.sha256_of_file(sandbox_root / rel)
    stale = ops_text.write_file_impl(
        rel,
        "row-1\nrow-2\n",
        expected_sha256="0" * 64,
        artifact_class="shared",
    )
    assert stale["reason"] == "file_sha256.mismatch"
    ok = ops_text.write_file_impl(
        rel,
        "row-1\nrow-2\n",
        expected_sha256=current,
        artifact_class="shared",
    )
    assert ok["status"] == "written"


def test_shared_append_stale_base_refuses(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/roadmaps/feature-alignment.md"
    ops_text.write_file_impl(rel, "head\n", artifact_class="shared")
    missing = ops_text.edit_file_impl(
        rel, "append", "rank-act\n", artifact_class="shared"
    )
    assert missing["reason"] == "expected_sha256.required"
    stale = ops_text.edit_file_impl(
        rel,
        "append",
        "rank-act\n",
        expected_sha256="0" * 64,
        artifact_class="shared",
    )
    assert stale["reason"] == "file_sha256.mismatch"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "head\n"


def test_dispatch_replace_shared_expected_sha256_succeeds_and_refuses(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatcher must forward expected_sha256 on replace (row-27 plumbing)."""
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(ops_text, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/demo-capability-roadmap.md"
    ops_text.write_file_impl(rel, "alpha\nbeta\n", artifact_class="shared")
    current = paths.sha256_of_file(sandbox_root / rel)

    missing = files_dispatcher.dispatch_files_op(
        op="replace",
        path=rel,
        target="beta",
        content="gamma",
    )
    assert missing["reason"] == "expected_sha256.required"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "alpha\nbeta\n"

    wrong = files_dispatcher.dispatch_files_op(
        op="replace",
        path=rel,
        target="beta",
        content="gamma",
        expected_sha256="0" * 64,
    )
    assert wrong["reason"] == "file_sha256.mismatch"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "alpha\nbeta\n"

    ok = files_dispatcher.dispatch_files_op(
        op="replace",
        path=rel,
        target="beta",
        content="gamma",
        expected_sha256=current,
    )
    assert ok.get("reason") is None
    assert "error" not in ok
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "alpha\ngamma\n"


def test_mint_full_execution_id_not_exec8() -> None:
    path_a = authority.mint_consult_artifact_rel_path(
        thread="6655",
        slug="soft-defer",
        seat="cdp-opus",
        execution_id="65b24006" + ("a" * 24),
        kind="architecture",
    )
    path_b = authority.mint_consult_artifact_rel_path(
        thread="6655",
        slug="soft-defer",
        seat="cdp-fable",
        execution_id="65b24006" + ("b" * 24),
        kind="architecture",
    )
    assert path_a != path_b
    assert "65b24006aaaaaaaaaaaaaaaaaaaaaaaa" in path_a
    assert "65b24006bbbbbbbbbbbbbbbbbbbbbbbb" in path_b


def test_episode_collision_replay_both_peers_survive(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay falsifier: same-fork dual write lands two digests at distinct URIs."""
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    opus_body = "# opus peer body for soft-defer bind\n"
    fable_body = "# fable peer body for soft-defer bind\n"
    opus_path = authority.mint_consult_artifact_rel_path(
        thread="6655",
        slug="mcp-soft-defer-remedy",
        seat="cdp-opus",
        execution_id="65b24006ff7c62cc25f4175d8c19c649",
        kind="architecture-bind",
    )
    fable_path = authority.mint_consult_artifact_rel_path(
        thread="6655",
        slug="mcp-soft-defer-remedy",
        seat="cdp-fable",
        execution_id="268f8754c441bdda6f3f8bec4db7e740",
        kind="architecture-bind",
    )
    assert opus_path != fable_path
    w1 = ops_text.write_file_impl(opus_path, opus_body, if_absent=True)
    w2 = ops_text.write_file_impl(fable_path, fable_body, if_absent=True)
    assert w1["status"] == "written"
    assert w2["status"] == "written"
    assert (sandbox_root / opus_path).read_text(encoding="utf-8") == opus_body
    assert (sandbox_root / fable_path).read_text(encoding="utf-8") == fable_body
    # Slug-only collision address refuses and keeps a pointer when contested.
    collision = "notes/system/threads/6655-mcp-soft-defer-remedy-architecture-bind.md"
    ops_text.write_file_impl(collision, opus_body, if_absent=True)
    clash = ops_text.write_file_impl(collision, fable_body, if_absent=True)
    assert clash["reason"] == "file_exists"
    assert clash.get("pointer_installed") is True
    # Distinct peer addresses still hold their bodies.
    assert (sandbox_root / opus_path).read_text(encoding="utf-8") == opus_body
    assert (sandbox_root / fable_path).read_text(encoding="utf-8") == fable_body
