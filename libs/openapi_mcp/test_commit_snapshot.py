"""Regression: OpenAPI pre-commit gate must judge the commit tree, not the WT.

Q2 shape (manifest 21 / committed routes 19 / foreign WIP in the worktree)
false-PASSed the old worktree gate and must FATAL the index gate. Matched
index 19/19 with unstaged WIP must PASS. A genuine index mismatch must still
FATAL so the gate does not go quiet.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from openapi_mcp.codegen import parse_manifest_source
from openapi_mcp.commit_snapshot import (
    check_committed_bindings,
    check_services_from_commit_tree,
    materialize_index,
    resulting_commit_text,
)

pytestmark = pytest.mark.offline

_LANE_BIND = {
    "lane_bind": {
        "method": "POST",
        "path": "/threads/{thread_id}/lane-bind",
        "operation_id": "lane_bind",
    },
    "lane_current": {
        "method": "GET",
        "path": "/threads/{thread_id}/lane-current",
        "operation_id": "lane_current",
    },
}
_MANIFEST = "libs/agent_bus_store/openapi_mcp/generated_adapter_manifest.py"
_LIVE = "libs/agent_bus_store/openapi_mcp/generated_live_ops.py"


def _ops(count: int, extra: dict[str, dict[str, str]] | None = None) -> dict:
    served = {
        f"op_{i}": {
            "method": "GET",
            "path": f"/p/{i}",
            "operation_id": f"op_{i}",
        }
        for i in range(count)
    }
    if extra:
        served.update(extra)
    return served


def _render(ops: dict[str, dict[str, str]]) -> str:
    return (
        'OPENAPI_SHA256 = "' + ("a" * 64) + '"\n'
        f"SERVED_OPS = {ops!r}\n"
        "NON_BINDING_PATH_FINGERPRINTS = {}\n"
        'FACADE_TOOL = "agent-bus"\n'
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "gate@test")
    _git(repo, "config", "user.name", "gate")
    return repo


def _write(repo: Path, relpath: str, text: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, relpaths: list[str], message: str) -> None:
    _git(repo, "add", "--", *relpaths)
    _git(repo, "commit", "-m", message)


def _live_loader_from_index(services: list[str], repo: Path):
    del services
    source = resulting_commit_text(repo, _LIVE)
    assert source is not None
    return {"agent-bus": parse_manifest_source(source)}


def test_q2_worktree_false_pass_is_index_fatal(tmp_path: Path) -> None:
    """Manifest 21 / committed routes 19 / WT 21: old gate PASS, new gate FATAL."""
    repo = _init_repo(tmp_path)
    head_ops = _ops(19)
    q2_manifest = _ops(19, _LANE_BIND)
    _write(repo, _MANIFEST, _render(head_ops))
    _write(repo, _LIVE, _render(head_ops))
    _commit(repo, [_MANIFEST, _LIVE], "head 19/19")
    _write(repo, _MANIFEST, _render(q2_manifest))
    _write(repo, _LIVE, _render(q2_manifest))
    _git(repo, "add", "--", _MANIFEST)

    wt_manifest = parse_manifest_source((repo / _MANIFEST).read_text())
    wt_live = parse_manifest_source((repo / _LIVE).read_text())
    old = check_committed_bindings(wt_manifest, wt_live)
    assert old.ok, old.fatal_messages
    assert old.fatal_messages == ()

    results = check_services_from_commit_tree(
        ["agent-bus"],
        repo=repo,
        live_loader=_live_loader_from_index,
    )
    assert len(results) == 1
    _, new = results[0]
    assert not new.ok
    joined = "\n".join(new.fatal_messages)
    # Manifest 21 vs committed routes 19: ops in the staged manifest are absent
    # from the committed route set ("lost"), which is the Q2 broken commit.
    assert (
        "FATAL: binding lost for op 'lane_bind' "
        "(POST /threads/{thread_id}/lane-bind)"
    ) in joined
    assert (
        "FATAL: binding lost for op 'lane_current' "
        "(GET /threads/{thread_id}/lane-current)"
    ) in joined


def test_matched_index_passes_with_foreign_worktree_wip(tmp_path: Path) -> None:
    """Index 19/19 with unstaged 21/21 WIP must PASS (path-explicit commit)."""
    repo = _init_repo(tmp_path)
    head_ops = _ops(19)
    _write(repo, _MANIFEST, _render(head_ops))
    _write(repo, _LIVE, _render(head_ops))
    _commit(repo, [_MANIFEST, _LIVE], "head 19/19")
    _write(repo, _MANIFEST, _render(_ops(19, _LANE_BIND)))
    _write(repo, _LIVE, _render(_ops(19, _LANE_BIND)))

    results = check_services_from_commit_tree(
        ["agent-bus"],
        repo=repo,
        live_loader=_live_loader_from_index,
    )
    _, new = results[0]
    assert new.ok, new.fatal_messages
    assert new.fatal_messages == ()


def test_genuine_index_mismatch_still_fatals(tmp_path: Path) -> None:
    """Staged extra live ops against a 19-op index manifest must still FATAL."""
    repo = _init_repo(tmp_path)
    head_ops = _ops(19)
    _write(repo, _MANIFEST, _render(head_ops))
    _write(repo, _LIVE, _render(head_ops))
    _commit(repo, [_MANIFEST, _LIVE], "head 19/19")
    _write(repo, _LIVE, _render(_ops(19, _LANE_BIND)))
    _git(repo, "add", "--", _LIVE)

    results = check_services_from_commit_tree(
        ["agent-bus"],
        repo=repo,
        live_loader=_live_loader_from_index,
    )
    _, new = results[0]
    assert not new.ok
    assert any("lane_bind" in msg for msg in new.fatal_messages)


def test_materialize_index_hides_unstaged_live_module(tmp_path: Path) -> None:
    """Live operand must come from the index, not an unstaged worktree edit."""
    repo = _init_repo(tmp_path)
    rel = "pkg/ops.py"
    _write(repo, "pkg/__init__.py", "")
    _write(repo, rel, "OPS = {'keep': 1}\n")
    _commit(repo, ["pkg/__init__.py", rel], "index ops")
    _write(repo, rel, "OPS = {'keep': 1, 'lane_bind': 1}\n")
    dest = tmp_path / "snap"
    materialize_index(repo, dest)
    ns: dict = {}
    exec(compile((dest / rel).read_text(), rel, "exec"), ns)
    assert ns["OPS"] == {"keep": 1}
    wt_ns: dict = {}
    exec(compile((repo / rel).read_text(), rel, "exec"), wt_ns)
    assert "lane_bind" in wt_ns["OPS"]
