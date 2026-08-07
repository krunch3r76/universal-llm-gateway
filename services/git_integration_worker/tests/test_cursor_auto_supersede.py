"""Same-thread supersede: interrupt, terminal vocabulary, revert, FIFO safety."""

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto import supersede as auto_supersede
from services.git_integration_worker.cursor_auto.queue import AutoJobQueue
from services.git_integration_worker.cursor_auto.supersede import (
    SUPERSEDED_TERMINAL,
    compose_supersede_preamble,
    post_superseded_terminal,
    supersede_same_thread_inflight,
    superseded_terminal_summary,
)
from services.git_integration_worker.cursor_sdk_revert import revert_dispatch_writes
from services.git_integration_worker.cursor_sdk_supersede import (
    is_dispatch_superseded,
    live_run_for_thread,
    register_live_run,
    signal_supersede,
    unregister_live_run,
)


def _enqueue(queue, *, thread_id, turn_number=1, contract="implement"):
    return queue.enqueue(
        thread_id=thread_id,
        turn_number=turn_number,
        subject=f"turn {turn_number}",
        body="## Scope\nlibs/foo\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract=contract,
    )


@pytest.fixture
def live_run():
    run = MagicMock()
    register_live_run(
        dispatch_id="auto-live1",
        thread_id="5867",
        source_repo="/repo",
        run=run,
    )
    yield run
    unregister_live_run(dispatch_id="auto-live1")
    auto_supersede._PENDING.clear()


def test_same_thread_request_supersedes_live_run(live_run):
    queue = AutoJobQueue()
    old = _enqueue(queue, thread_id="5867", turn_number=8)
    assert queue.claim_next().job_id == old.job_id
    new = _enqueue(queue, thread_id="5867", turn_number=9)

    evidence = asyncio.run(supersede_same_thread_inflight(new, queue=queue))

    live_run.cancel.assert_called_once()
    assert evidence["method"] == "run_cancel"
    assert evidence["superseded_dispatch_id"] == "auto-live1"
    assert queue.is_superseded(old.job_id)
    assert queue.get(old.job_id).superseded_by == new.job_id
    assert new.supersedes == old.job_id
    assert is_dispatch_superseded(dispatch_id="auto-live1")


def test_cross_thread_request_stays_fifo(live_run):
    queue = AutoJobQueue()
    old = _enqueue(queue, thread_id="5867", turn_number=8)
    queue.claim_next()
    other = _enqueue(queue, thread_id="9999", turn_number=1)

    evidence = asyncio.run(supersede_same_thread_inflight(other, queue=queue))

    assert evidence is None
    live_run.cancel.assert_not_called()
    assert queue.get(old.job_id).status == "claimed"
    assert other.supersedes is None
    assert queue.snapshot()["pending"] == 1


def test_supersede_escalates_to_bridge_abort_when_cancel_refused(monkeypatch):
    run = MagicMock()
    run.cancel.side_effect = RuntimeError("run already terminal")
    aborted: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_supersede.abort_orphaned_bridge",
        lambda *, dispatch_id: aborted.append(dispatch_id) or True,
    )
    register_live_run(
        dispatch_id="auto-refuse",
        thread_id="7001",
        source_repo="/repo",
        run=run,
    )
    try:
        evidence = signal_supersede(
            dispatch_id="auto-refuse",
            superseded_by="job-new",
            reason="test",
        )
    finally:
        unregister_live_run(dispatch_id="auto-refuse")

    assert evidence["method"] == "bridge_abort"
    assert aborted == ["auto-refuse"]
    assert live_run_for_thread("7001") is None


def test_mark_done_cannot_overwrite_superseded_status():
    queue = AutoJobQueue()
    job = _enqueue(queue, thread_id="5867")
    queue.claim_next()
    queue.mark_superseded(job.job_id, superseded_by="job-new")

    queue.mark_done(job.job_id, failed=False)

    assert queue.get(job.job_id).status == "superseded"
    assert queue.snapshot()["superseded"] == 1
    assert queue.snapshot()["done"] == 0


def test_poll_returns_superseded_without_waiting_for_ledger():
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        poll_dispatch_terminal,
    )

    polled = asyncio.run(
        poll_dispatch_terminal(
            thread_id="5867",
            dispatch_id="auto-live1",
            timeout_s=30.0,
            superseded=lambda: True,
        )
    )

    assert polled == {
        "ok": False,
        "terminal": False,
        "superseded": True,
        "dispatch_id": "auto-live1",
    }


def test_process_job_superseded_before_submit_posts_superseded_terminal(monkeypatch):
    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import get_queue

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    submit = AsyncMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize."
        "sdk_dispatch_gate_stats",
        lambda **_kw: {"active": 0, "queued": 0, "limit": 1},
    )

    queue = get_queue()
    job = _enqueue(queue, thread_id="5867", turn_number=8)
    queue.claim_next()
    queue.mark_superseded(job.job_id, superseded_by="job-new")

    result = asyncio.run(process_job(job, bus=bus))

    assert result["terminal_status"] == SUPERSEDED_TERMINAL
    assert result["phase"] == "superseded"
    submit.assert_not_awaited()
    terminal = bus.reply.await_args_list[-1]
    assert terminal.kwargs["subject"].startswith(SUPERSEDED_TERMINAL)
    assert "status:done" not in terminal.kwargs["subject"]
    payload = json.loads(terminal.kwargs["body"])
    assert payload["superseded_by_job"] == "job-new"
    assert payload["terminal_vocabulary"] == SUPERSEDED_TERMINAL


def test_process_job_superseded_mid_poll_skips_closeout_relay(monkeypatch):
    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import get_queue

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        AsyncMock(return_value={"ok": True, "dispatch_id": "auto-mid"}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.poll_dispatch_terminal",
        AsyncMock(
            return_value={"ok": False, "terminal": False, "superseded": True}
        ),
    )
    relay = AsyncMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome."
        "post_operator_closeout",
        relay,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize."
        "sdk_dispatch_gate_stats",
        lambda **_kw: {"active": 0, "queued": 0, "limit": 1},
    )

    job = _enqueue(get_queue(), thread_id="5867", turn_number=9)
    job.superseded_by = "job-newer"

    result = asyncio.run(process_job(job, bus=bus))

    assert result["terminal_status"] == SUPERSEDED_TERMINAL
    assert result["dispatch_id"] == "auto-mid"
    relay.assert_not_awaited()


def test_revert_settles_even_when_superseding_job_is_refused(monkeypatch):
    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import get_queue

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    settled: list[str] = []

    async def _settle(job):
        settled.append(job.job_id)
        return None

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.settle_supersede",
        _settle,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize."
        "sdk_dispatch_gate_stats",
        lambda **_kw: {"active": 0, "queued": 0, "limit": 1},
    )

    job = _enqueue(get_queue(), thread_id="5867", turn_number=10)
    job.require_attended = True

    result = asyncio.run(process_job(job, bus=bus))

    assert result["terminal_status"] == "status:needs-attended"
    assert settled == [job.job_id], "void episode must be reverted before refusal"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "tracked.py").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    return repo


def _stub_ledger(monkeypatch, baseline, *, live_leases: int = 1):
    ledger = MagicMock()
    ledger.read_wt_baseline.return_value = baseline
    ledger.count_active_write_leases.return_value = live_leases
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_revert.CursorDispatchLedger."
        "instance",
        classmethod(lambda cls: ledger),
    )


def test_revert_restores_tracked_writes_and_leaves_untracked(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _stub_ledger(monkeypatch, {"codes": {}, "hashes": {}})
    (repo / "tracked.py").write_text("episode wrote this\n")
    (repo / "new_file.py").write_text("episode created this\n")

    report = revert_dispatch_writes(dispatch_id="auto-live1", source_repo=repo)

    assert report.ok is True
    assert "tracked.py" in report.restored
    assert (repo / "tracked.py").read_text() == "original\n"
    assert "new_file.py" in report.created_left
    assert (repo / "new_file.py").exists(), "untracked paths are never auto-deleted"


def test_revert_fails_closed_without_baseline(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _stub_ledger(monkeypatch, None)

    report = revert_dispatch_writes(dispatch_id="auto-live1", source_repo=repo)

    assert report.ok is False
    assert report.reason == "baseline_unavailable"
    assert report.restored == ()


def test_revert_refuses_when_multiple_write_leases_live(tmp_path, monkeypatch):
    from services.git_integration_worker.cursor_sdk_closeout import (
        capture_wt_baseline_with_hashes,
    )

    repo = _init_repo(tmp_path)
    baseline = capture_wt_baseline_with_hashes(repo)
    assert baseline is not None
    _stub_ledger(monkeypatch, baseline, live_leases=2)
    (repo / "tracked.py").write_text("episode wrote this\n")

    report = revert_dispatch_writes(dispatch_id="auto-live1", source_repo=repo)

    assert report.ok is False
    assert report.reason == "multiple_write_leases_live"
    assert report.restored == ()
    assert report.unrevertable == ("tracked.py",)
    assert (repo / "tracked.py").read_text() == "episode wrote this\n"


def test_revert_single_lease_equivalence_unchanged(tmp_path, monkeypatch):
    from services.git_integration_worker.cursor_sdk_closeout import (
        capture_wt_baseline_with_hashes,
    )

    repo = _init_repo(tmp_path)
    baseline = capture_wt_baseline_with_hashes(repo)
    assert baseline is not None
    _stub_ledger(monkeypatch, baseline, live_leases=1)
    (repo / "tracked.py").write_text("episode wrote this\n")

    report = revert_dispatch_writes(dispatch_id="auto-live1", source_repo=repo)

    assert report.ok is True
    assert report.restored == ("tracked.py",)
    assert (repo / "tracked.py").read_text() == "original\n"
    assert report.reason is None


def test_preamble_names_void_episode_and_residue():
    text = compose_supersede_preamble(
        {
            "mark": {"dispatch_id": "auto-live1", "method": "run_cancel"},
            "revert": {
                "dispatch_id": "auto-live1",
                "ok": False,
                "restored": ["a.py"],
                "created_left": ["b.py"],
                "unrevertable": ["c.py"],
                "reason": "unrevertable_paths_present",
            },
        }
    )

    assert "auto-live1" in text
    assert "void" in text
    assert "a.py" in text and "b.py" in text and "c.py" in text
    assert "revert INCOMPLETE" in text


def test_nested_sdk_finished_excludes_from_supersede_without_mark_done():
    """INV-CLAIM-WINDOW-AUTHORITY falsifier: exclude sdk_terminal without mark_done.

    Removing ``not job.nested_sdk_finished`` from ``claimed_for_thread`` must fail
    this test — otherwise ``no closeout is authoritative`` becomes a lie after
    nested SDK terminal while CLOSEOUT still relays.
    """
    queue = AutoJobQueue(durable=False)
    old = _enqueue(queue, thread_id="5867", turn_number=8)
    queue.claim_next()
    queue.mark_nested_sdk_finished(old.job_id)
    assert queue.get(old.job_id).status == "claimed"
    assert queue.claimed_for_thread("5867") is None

    new = _enqueue(queue, thread_id="5867", turn_number=9)
    evidence = asyncio.run(supersede_same_thread_inflight(new, queue=queue))

    assert evidence is None
    assert queue.get(old.job_id).status == "claimed"
    assert not queue.is_superseded(old.job_id)
    assert new.supersedes is None


def test_queued_only_still_supersedes_pre_submit_claimed_job():
    """Protects lane displacement for claimed-pre-live jobs (A1 honesty).

    A newer same-thread request must still mark the predecessor superseded so
    the lane progresses. It must **not** lock ``method=queued_only`` /
    cancel-before-start vocabulary — ``¬live`` is true for never-submitted and
    for bind→``register_live_run``, so the honest token is
    ``pre_register_live_run``.
    """
    queue = AutoJobQueue(durable=False)
    old = _enqueue(queue, thread_id="5867", turn_number=8)
    queue.claim_next()
    assert queue.get(old.job_id).nested_sdk_finished is False

    new = _enqueue(queue, thread_id="5867", turn_number=9)
    evidence = asyncio.run(supersede_same_thread_inflight(new, queue=queue))

    assert evidence is not None
    assert evidence["method"] == auto_supersede.PRE_REGISTER_LIVE_RUN
    assert evidence["method"] != "queued_only"
    assert queue.is_superseded(old.job_id)
    assert new.supersedes == old.job_id


def test_superseded_terminal_summary_replay_auto_47cdf529c125():
    """Falsifier: live dispatch interrupt must not claim work was undone."""
    summary, disposition = superseded_terminal_summary(
        superseded_by="e13bae97-428c-48af-bd88-9e8beed8f28f",
        dispatch_id="auto-47cdf529c125",
    )
    assert disposition == "revert-pending"
    assert summary == (
        "Episode superseded by a newer same-thread request "
        "(job e13bae97-428c-48af-bd88-9e8beed8f28f); episode void; "
        "revert-pending (successor settle reports tree); "
        "no closeout is authoritative."
    )
    assert "work reverted" not in summary
    assert "reverted-with-report" not in summary


def test_superseded_terminal_queued_only_is_revert_skipped():
    summary, disposition = superseded_terminal_summary(
        superseded_by="job-new",
        dispatch_id=None,
    )
    assert disposition == "revert-skipped"
    assert "revert-skipped" in summary
    assert "work reverted" not in summary


def test_post_superseded_terminal_payload_carries_revert_disposition():
    queue = AutoJobQueue(durable=False)
    old = _enqueue(queue, thread_id="5867", turn_number=8)
    queue.claim_next()
    new = _enqueue(queue, thread_id="5867", turn_number=9)
    queue.mark_superseded(old.job_id, superseded_by=new.job_id)
    old.superseded_by = new.job_id

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    asyncio.run(
        post_superseded_terminal(
            old, client=bus, queue=queue, dispatch_id="auto-47cdf529c125"
        )
    )
    payload = json.loads(bus.reply.await_args.kwargs["body"])
    assert payload["revert_disposition"] == "revert-pending"
    assert "work reverted" not in payload["summary"]
    # Member 3: additive claim_register on dispositional summary (derived).
    assert payload["claim_register"]["register"] == "derived"
    assert payload["claim_register"]["value"] == payload["summary"]
    assert payload["claim_register"]["basis"] == "supersede.dispositional_summary"
