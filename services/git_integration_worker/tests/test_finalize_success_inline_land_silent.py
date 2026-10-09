"""Friction 38968 — ``_finalize_success`` must pass inline ``message`` as ``packet_text``."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
    SdkRunOutcome,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    branch_state,
    commit_on_terminal,
)
from services.git_integration_worker.cursor_sdk_worktree import resolve_master_branch_point
from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest
from services.git_integration_worker.routes import cursor_sdk as route_mod
from services.git_integration_worker.tests.test_lane_b_hub_master_landed_resolution import (
    _cfg,
    _git,
    _lane_b_binding,
    _outcome,
    _settle_lane_b,
)

pytest_plugins = [
    "services.git_integration_worker.tests.test_lane_b_hub_master_landed_resolution",
]

# Line-start opt-in token (matches ``_SILENT_LAND_OPT_IN_RE`` in branch_terminal).
OPT_IN_LINE = "land" + ": " + "silent"


def _mint_lane_with_commit(
    source_repo: Path, tmp_path: Path, dispatch_id: str
) -> tuple[Path, CaptureBinding, str]:
    worktree_root = tmp_path / "worktrees"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/lane-{dispatch_id}"
    (wt / "lane_only.py").write_text("lane\n", encoding="utf-8")
    commit_on_terminal(
        dispatch_id=dispatch_id,
        worktree_path=wt,
        branch_name=branch,
    )
    branch_point = resolve_master_branch_point(source_repo)
    state = branch_state(
        source_repo, branch_name=branch, branch_point=branch_point
    )
    assert state.commits_ahead is not None and state.commits_ahead >= 1, (
        dispatch_id,
        state.commits_ahead,
    )
    tip = _git("rev-parse", branch, cwd=source_repo).stdout.strip()
    cfg = _cfg(source_repo, worktree_root)
    binding = _lane_b_binding(cfg, wt)
    return wt, binding, tip


def _lane_b_req(
    *,
    dispatch_id: str,
    message: str | None = None,
    packet_path: str | None = None,
) -> CursorDispatchRequest:
    return CursorDispatchRequest(
        thread_id="t-finalize-inline-silent",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        handoff_contract="none",
        lane="B",
        message=message,
        packet_path=packet_path,
    )


@pytest.mark.asyncio
async def test_finalize_success_inline_message_passes_opt_in_to_closeout(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inline ``message`` with ``land: silent`` → captured packet_text lands on hub master."""
    dispatch_id = "auto-38968-inline-token"
    _wt, binding, tip = _mint_lane_with_commit(source_repo, tmp_path, dispatch_id)
    message = f"---\ncontract: none\n---\nwork\n{OPT_IN_LINE}\n"
    req = _lane_b_req(dispatch_id=dispatch_id, message=message)

    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> None:
        captured["packet_text"] = str(kwargs.get("packet_text", ""))

    monkeypatch.setattr(route_mod, "_deliver_sdk_closeout", _capture)
    await route_mod._finalize_success(
        req=req,
        source_repo=source_repo,
        binding=binding,
        outcome=_outcome(),
        bus=MagicMock(),
        reply_to="dispatch",
        controller=MagicMock(),
        worktree_isolated=True,
    )
    packet_text = captured["packet_text"]
    assert OPT_IN_LINE in packet_text

    landed, head_sha, _ahead, _reason = _settle_lane_b(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        files_outside_repo=(),
        packet_text=packet_text,
    )
    assert landed is True
    assert head_sha == tip
    ancestor = subprocess.run(
        ["git", "-C", str(source_repo), "merge-base", "--is-ancestor", tip, "master"],
        capture_output=True,
        check=False,
    )
    assert ancestor.returncode == 0


@pytest.mark.asyncio
async def test_finalize_success_inline_without_token_does_not_land(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inline message without opt-in → empty effective packet_text at settle → no hub merge."""
    dispatch_id = "auto-38968-inline-no-token"
    _wt, binding, tip = _mint_lane_with_commit(source_repo, tmp_path, dispatch_id)
    master_before = _git("rev-parse", "master", cwd=source_repo).stdout.strip()
    req = _lane_b_req(
        dispatch_id=dispatch_id,
        message="---\ncontract: none\n---\nwork only\n",
    )

    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> None:
        captured["packet_text"] = str(kwargs.get("packet_text", ""))

    monkeypatch.setattr(route_mod, "_deliver_sdk_closeout", _capture)
    await route_mod._finalize_success(
        req=req,
        source_repo=source_repo,
        binding=binding,
        outcome=_outcome(),
        bus=MagicMock(),
        reply_to="dispatch",
        controller=MagicMock(),
        worktree_isolated=True,
    )
    packet_text = captured["packet_text"]

    landed, head_sha, _ahead, _reason = _settle_lane_b(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        files_outside_repo=(),
        packet_text=packet_text,
    )
    assert landed is not True
    assert head_sha == tip
    assert _git("rev-parse", "master", cwd=source_repo).stdout.strip() == master_before


@pytest.mark.asyncio
async def test_finalize_success_packet_path_passes_opt_in_to_closeout(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``packet_path`` file with opt-in still reaches closeout and lands."""
    dispatch_id = "auto-38968-packet-path-token"
    gitignore = source_repo / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("tmp/\n", encoding="utf-8")
        _git("add", ".gitignore", cwd=source_repo)
        _git("commit", "-m", "gitignore tmp for packet_path fixture", cwd=source_repo)
    _wt, binding, tip = _mint_lane_with_commit(source_repo, tmp_path, dispatch_id)
    rel = "tmp/packets/dispatch.md"
    packet_file = source_repo / rel
    packet_file.parent.mkdir(parents=True, exist_ok=True)
    packet_file.write_text(f"{OPT_IN_LINE}\n", encoding="utf-8")
    req = _lane_b_req(dispatch_id=dispatch_id, message=None, packet_path=rel)

    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> None:
        captured["packet_text"] = str(kwargs.get("packet_text", ""))

    expected_packet = route_mod._read_packet_text(req, source_repo)
    assert OPT_IN_LINE in expected_packet

    monkeypatch.setattr(route_mod, "_deliver_sdk_closeout", _capture)
    await route_mod._finalize_success(
        req=req,
        source_repo=source_repo,
        binding=binding,
        outcome=_outcome(),
        bus=MagicMock(),
        reply_to="dispatch",
        controller=MagicMock(),
        worktree_isolated=True,
    )
    packet_text = captured["packet_text"]
    assert packet_text == expected_packet

    landed, head_sha, _ahead, _reason = _settle_lane_b(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        files_outside_repo=(),
        packet_text=packet_text,
    )
    assert landed is True
    assert head_sha == tip
    ancestor = subprocess.run(
        ["git", "-C", str(source_repo), "merge-base", "--is-ancestor", tip, "master"],
        capture_output=True,
        check=False,
    )
    assert ancestor.returncode == 0
