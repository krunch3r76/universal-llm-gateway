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
