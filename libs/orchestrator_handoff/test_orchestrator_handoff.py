"""Tests for orchestrator handoff queue + repair."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orchestrator_handoff.queue import HandoffQueue
from orchestrator_handoff.repair import repair_tick


def _write_index(path: Path, prompt: str) -> None:
    path.write_text(
        f"| `{prompt}` | **ready** | P0 | test row |\n",
        encoding="utf-8",
    )


def test_import_skips_done_prompt(tmp_path: Path, monkeypatch) -> None:
    import orchestrator_handoff.queue as queue_mod

    monkeypatch.setattr(queue_mod, "_REPO", tmp_path)
    prompts = tmp_path / "tmp/prompts"
    prompts.mkdir(parents=True)
    (prompts / "tab-launch-work-foo.md").write_text("# x\n", encoding="utf-8")
    qpath = tmp_path / "queue.json"
    index = tmp_path / "index.md"
    _write_index(index, "tab-launch-work-foo.md")
    q = HandoffQueue.open(path=qpath)
    first = q.import_ready_index_rows(index)
    assert first["imported"]
    item_id = first["imported"][0]
    q.complete(item_id)
    second = q.import_ready_index_rows(index)
    assert second["skipped"]
    assert not second["imported"]


def test_import_requeues_after_cancelled(tmp_path: Path, monkeypatch) -> None:
    import orchestrator_handoff.queue as queue_mod

    monkeypatch.setattr(queue_mod, "_REPO", tmp_path)
    prompts = tmp_path / "tmp/prompts"
    prompts.mkdir(parents=True)
    (prompts / "tab-launch-work-foo.md").write_text("# x\n", encoding="utf-8")
    qpath = tmp_path / "queue.json"
    index = tmp_path / "index.md"
    _write_index(index, "tab-launch-work-foo.md")
    q = HandoffQueue.open(path=qpath)
    first = q.import_ready_index_rows(index)
    assert first["imported"]
    item_id = first["imported"][0]
    q.cancel(item_id)
    second = q.import_ready_index_rows(index)
    assert second["imported"]
    assert not second["skipped"]


def test_requeue_launch_failure_then_terminal(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    q = HandoffQueue.open(path=qpath)
    out = q.enqueue(intent="t", work_prompt=__file__, priority="P0")
    item_id = out["item"]["id"]
    q.mark_launching(item_id)
    r1 = q.requeue_launch_failure(item_id, "keystroke", max_attempts=3)
    assert r1["item"]["status"] == "queued"
    assert r1["item"]["attempts"] == 1
    q.mark_launching(item_id)
    r2 = q.requeue_launch_failure(item_id, "keystroke", max_attempts=3)
    assert r2["item"]["attempts"] == 2
    q.mark_launching(item_id)
    r3 = q.requeue_launch_failure(item_id, "keystroke", max_attempts=3)
    assert r3["item"]["status"] == "failed"


def test_repair_orphan_launching(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    lock_path = tmp_path / "lock.json"
    q = HandoffQueue.open(path=qpath)
    out = q.enqueue(intent="t", work_prompt=__file__, priority="P0")
    item_id = out["item"]["id"]
    data = json.loads(qpath.read_text())
    for item in data["items"]:
        if item["id"] == item_id:
            item["status"] = "launching"
            item["started_at"] = (datetime.now(UTC) - timedelta(minutes=20)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
    qpath.write_text(json.dumps(data, indent=2), encoding="utf-8")
    repairs = repair_tick(q, lock_path=lock_path)
    assert any(r["action"] == "orphan_launching" for r in repairs)
    assert q.get(item_id)["status"] == "queued"


def test_repair_stale_lock_acked_without_queue_id(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    lock_path = tmp_path / "lock.json"
    q = HandoffQueue.open(path=qpath)
    lock_path.write_text(
        json.dumps(
            {
                "holder": "tab-dead",
                "intent": "manual",
                "acked_at": (datetime.now(UTC) - timedelta(minutes=30)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "updated_at": (datetime.now(UTC) - timedelta(minutes=30)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
        ),
        encoding="utf-8",
    )
    repairs = repair_tick(q, lock_path=lock_path)
    assert any(r["action"] == "stale_lock_acked" for r in repairs)
    assert not lock_path.is_file()


def test_repair_stale_lock_no_timestamp(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    lock_path = tmp_path / "lock.json"
    q = HandoffQueue.open(path=qpath)
    lock_path.write_text(
        json.dumps({"holder": "orphan", "intent": "x"}), encoding="utf-8"
    )
    repairs = repair_tick(q, lock_path=lock_path)
    assert any(r["action"] == "stale_lock_no_timestamp" for r in repairs)
    assert not lock_path.is_file()


def test_repair_stale_launch_lock_matching_queue_id(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    lock_path = tmp_path / "lock.json"
    q = HandoffQueue.open(path=qpath)
    out = q.enqueue(intent="t", work_prompt=__file__, priority="P0")
    item_id = out["item"]["id"]
    stale = (datetime.now(UTC) - timedelta(minutes=17)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = json.loads(qpath.read_text())
    for item in data["items"]:
        if item["id"] == item_id:
            item["status"] = "launching"
            item["started_at"] = stale
    qpath.write_text(json.dumps(data, indent=2), encoding="utf-8")
    lock_path.write_text(
        json.dumps(
            {
                "holder": "keystroke-dead",
                "intent": "t",
                "queue_id": item_id,
                "acked_at": stale,
                "updated_at": stale,
                "acquired_at": stale,
            }
        ),
        encoding="utf-8",
    )
    repairs = repair_tick(q, lock_path=lock_path)
    assert any(r["action"] == "stale_launch_lock" for r in repairs)
    assert not lock_path.is_file()
    assert q.get(item_id)["status"] == "queued"


def test_repair_regreenlit_ready(tmp_path: Path, monkeypatch) -> None:
    import orchestrator_handoff.queue as queue_mod

    monkeypatch.setattr(queue_mod, "_REPO", tmp_path)
    prompts = tmp_path / "tmp/prompts"
    prompts.mkdir(parents=True)
    (prompts / "tab-launch-work-foo.md").write_text("# x\n", encoding="utf-8")
    qpath = tmp_path / "queue.json"
    index = tmp_path / "index.md"
    _write_index(index, "tab-launch-work-foo.md")
    q = HandoffQueue.open(path=qpath)
    out = q.enqueue(
        intent="tab-launch-work-foo",
        work_prompt="tmp/prompts/tab-launch-work-foo.md",
        priority="P0",
    )
    q.complete(out["item"]["id"])
    repairs = repair_tick(q, lock_path=tmp_path / "lock.json", index_path=index)
    assert any(r["action"] == "regreenlit_ready" for r in repairs)
    assert q.peek() is not None
