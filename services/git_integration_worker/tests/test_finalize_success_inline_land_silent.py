"""Friction 38968 — inline ``land: silent`` uses ``land_text``; ``packet_text`` stays empty."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    branch_state,
    commit_on_terminal,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    mint_dispatch_worktree,
    resolve_master_branch_point,
)
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


def _settle_lane_b_with_land(
    *,
    source_repo: Path,
    binding: CaptureBinding,
    dispatch_id: str,
    packet_text: str | None,
    land_text: str | None,
) -> tuple[bool | None, str | None, int | None, str | None]:
    from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
    from services.git_integration_worker.cursor_sdk_closeout.delivery_assembly.lane_settlement import (
        settle_lane_and_dispatch_fields,
    )

    fields = settle_lane_and_dispatch_fields(
        binding=binding,
        dispatch_id=dispatch_id,
        write_tree=binding.write_tree,
        receipt_tree=binding.receipt_tree,
        repo_change_set=ChangeSet(created=(), modified=(), deleted=()),
        outcome=_outcome(),
        deviations=[],
        divergence_reason=None,
        baseline=None,
        files_untracked_or_ignored=(),
        offgit_uris=(),
        thread_id="t-landed-resolution",
        gate_d_created_rels=(),
        files_outside_repo=(),
        packet_text=packet_text,
        land_text=land_text,
    )
    (
        _lane,
        _branch,
        _branch_point,
        head_sha,
        commits_ahead,
        _unfiltered,
        landed,
        *_rest,
    ) = fields
    landed_resolution_reason = fields[-1]
    return landed, head_sha, commits_ahead, landed_resolution_reason


@pytest.mark.asyncio
async def test_finalize_success_inline_message_passes_opt_in_to_closeout(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inline ``message`` with ``land: silent`` → ``land_text`` lands; ``packet_text`` empty."""
    dispatch_id = "auto-38968-inline-token"
    _wt, binding, tip = _mint_lane_with_commit(source_repo, tmp_path, dispatch_id)
    message = f"---\ncontract: none\n---\nwork\n{OPT_IN_LINE}\n"
    req = _lane_b_req(dispatch_id=dispatch_id, message=message)

    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> None:
        captured["packet_text"] = str(kwargs.get("packet_text", ""))
        captured["land_text"] = str(kwargs.get("land_text", ""))

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
    assert captured["packet_text"] == ""
    assert OPT_IN_LINE in captured["land_text"]

    landed, head_sha, _ahead, _reason = _settle_lane_b_with_land(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        packet_text="",
        land_text=message,
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
    """Inline message without opt-in → empty ``packet_text``; no hub merge."""
    dispatch_id = "auto-38968-inline-no-token"
    _wt, binding, tip = _mint_lane_with_commit(source_repo, tmp_path, dispatch_id)
    master_before = _git("rev-parse", "master", cwd=source_repo).stdout.strip()
    message = "---\ncontract: none\n---\nwork only\n"
    req = _lane_b_req(dispatch_id=dispatch_id, message=message)

    captured: dict[str, str] = {}

    async def _capture(**kwargs: object) -> None:
        captured["packet_text"] = str(kwargs.get("packet_text", ""))
        captured["land_text"] = str(kwargs.get("land_text", ""))

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
    assert captured["packet_text"] == ""
    assert captured["land_text"] == message

    landed, head_sha, _ahead, _reason = _settle_lane_b_with_land(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        packet_text="",
        land_text=message,
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
        captured["land_text"] = str(kwargs.get("land_text", ""))

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
    assert captured["land_text"] == expected_packet

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
async def test_finalize_success_inline_todo_line_not_in_packet_text_readers(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inline ``todo:`` in ``message`` must not populate ``packet_text`` closeout readers."""
    dispatch_id = "auto-38968-inline-todo-guard"
    _wt, binding, _tip = _mint_lane_with_commit(source_repo, tmp_path, dispatch_id)
    todo_slug = "todo:friction-38968-close-guard"
    message = f"---\ntodo: {todo_slug}\ncontract: none\n---\nwork\n"
    req = _lane_b_req(dispatch_id=dispatch_id, message=message)

    captured: dict[str, object] = {}

    async def _capture(**kwargs: object) -> None:
        captured["packet_text"] = kwargs.get("packet_text", "")
        captured["land_text"] = kwargs.get("land_text", "")
        captured["work_item_ref"] = kwargs.get("work_item_ref")

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
    assert captured["packet_text"] == ""
    assert todo_slug in str(captured["land_text"])
    assert captured["work_item_ref"] is None


def test_debt_opened_emits_land_silent_token_absent_reason(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing opt-in on the no-disposition debt path tags ``land:silent_token_absent``."""
    from services.git_integration_worker.cursor_sdk_branch_terminal import (
        settle_lane_branch,
    )

    dispatch_id = "auto-38968-debt-reason"
    branch = f"cursor-sdk/lane-{dispatch_id}"
    _git("checkout", "-b", branch, cwd=source_repo)
    (source_repo / "lane_only.py").write_text("lane\n", encoding="utf-8")
    _git("add", "lane_only.py", cwd=source_repo)
    _git("commit", "-m", "lane", cwd=source_repo)
    tip = _git("rev-parse", "HEAD", cwd=source_repo).stdout.strip()
    _git("checkout", "master", cwd=source_repo)

    emitted: list[dict[str, object]] = []

    def _capture_emit(**kwargs: object) -> None:
        emitted.append(dict(kwargs))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_terminal.emit_sdk_lane_b_debt_opened",
        _capture_emit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_terminal.open_branch_debt",
        lambda **_kw: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_terminal.add_land_required_tag",
        lambda **_kw: None,
    )

    with caplog.at_level("WARNING"):
        settlement = settle_lane_branch(
            source_repo=source_repo,
            branch_name=branch,
            thread_id="t-debt-reason",
            dispatch_id=dispatch_id,
            closeout_text="status: complete",
            commits_ahead=1,
            landed=False,
            head_sha=tip,
            files=["lane_only.py"],
            packet_text="work without silent land opt-in\n",
        )

    assert settlement.outcome == "debt_opened"
    assert emitted
    assert emitted[-1].get("reason") == "land:silent_token_absent"
    assert any(
        "debt_reason=land:silent_token_absent" in record.message
        for record in caplog.records
    )
