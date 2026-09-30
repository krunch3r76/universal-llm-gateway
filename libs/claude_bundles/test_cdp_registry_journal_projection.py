"""Attachment fold reads the projection, never the whole registry.jsonl (a:36920)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claude_bundles import cdp_registry_store as store
from claude_bundles.cdp_registry import journal_projection as proj
from claude_bundles.cdp_registry.attachment_journal import (
    append_attachment_journal,
    fold_attachment_journal,
    has_attachment_observed,
    has_standdown_token,
)

pytestmark = pytest.mark.offline

_RID = "rid0000000000000000000000000000aa"
_CSE = "https://claude.ai/cowork/cse_projection_test"
_BULK_ROWS = 20_000


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    (root / "registrations").mkdir(parents=True)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    monkeypatch.setattr(store, "REGISTRY_DIR", root)
    monkeypatch.setattr(store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(store, "REGISTRATIONS_DIR", root / "registrations")
    store.write_active(
        {_RID: {"registration_id": _RID, "status": "active", "port": 9223}}
    )
    return root


def _bulk_fill(log: Path, rows: int) -> None:
    """Append *rows* non-fold lines the way ``append_log`` serializes them."""
    with log.open("a", encoding="utf-8") as fh:
        for i in range(rows):
            event = "cse.provenance.episode" if i % 2 else "hygiene_reclaim"
            fh.write(
                json.dumps(
                    {
                        "event": event,
                        "ts": time.time(),
                        "registration_id": _RID,
                        "i": i,
                    },
                    sort_keys=True,
                )
                + "\n"
            )


def _forbid_full_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> list[dict]:
        raise AssertionError("fold path parsed the whole registry.jsonl")

    monkeypatch.setattr(store, "read_registry_log", _boom)


def test_fold_reads_only_tail_and_projection(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = isolated_registry / "registry.jsonl"
    _bulk_fill(log, _BULK_ROWS)
    append_attachment_journal(
        registration_id=_RID, chat_url=_CSE, attach_proof="streaming"
    )
    _forbid_full_read(monkeypatch)

    fold_attachment_journal()
    assert store.load_active()[_RID].get("attached_at")

    projection = proj.projection_path()
    projected = [json.loads(line) for line in projection.read_text().splitlines()]
    assert [r["event"] for r in projected] == ["attachment_observed"]
    assert projection.stat().st_size < log.stat().st_size / 1000

    # Second fold: only the attachment_bound line the first fold appended is new.
    with store.ports_lock():
        receipt = proj.project_fold_tail()
    assert receipt.rows_consumed == 1
    assert receipt.rows_projected == 1
    assert not receipt.rebuilt
    tail_line = log.read_bytes().splitlines(keepends=True)[-1]
    assert receipt.bytes_consumed == len(tail_line)

    with store.ports_lock():
        assert proj.project_fold_tail().bytes_consumed == 0
    fold_attachment_journal()
    assert has_attachment_observed(_RID, _CSE)
    with store.ports_lock():
        assert not has_standdown_token(_RID)


def test_projection_pass_receipt_measures_appended_bytes(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = isolated_registry / "registry.jsonl"
    _bulk_fill(log, _BULK_ROWS)
    with store.ports_lock():
        first = proj.project_fold_tail()
    assert first.rebuilt
    assert first.rows_consumed == _BULK_ROWS
    assert first.bytes_consumed == log.stat().st_size
    assert first.rows_projected == 0

    _forbid_full_read(monkeypatch)
    before = log.stat().st_size
    store.append_log("standdown_pasted", {"registration_id": _RID, "chat_url": _CSE})
    with store.ports_lock():
        second = proj.project_fold_tail()
    assert second.bytes_consumed == log.stat().st_size - before
    assert (second.rows_consumed, second.rows_projected) == (1, 1)
    with store.ports_lock():
        assert has_standdown_token(_RID)


def test_partial_trailing_line_waits_for_next_pass(isolated_registry: Path) -> None:
    log = isolated_registry / "registry.jsonl"
    store.append_log("attachment_observed", {"registration_id": _RID, "chat_url": _CSE})
    with log.open("ab") as fh:
        fh.write(b'{"event": "detached", "registration_id": "' + _RID.encode())
    with store.ports_lock():
        receipt = proj.project_fold_tail()
    assert (receipt.rows_consumed, receipt.rows_projected) == (1, 1)
    with log.open("ab") as fh:
        fh.write(b'"}\n')
    with store.ports_lock():
        receipt = proj.project_fold_tail()
    assert (receipt.rows_consumed, receipt.rows_projected) == (1, 1)
    assert [
        json.loads(r)["event"] for r in proj.projection_path().read_text().splitlines()
    ] == [
        "attachment_observed",
        "detached",
    ]


def test_truncated_journal_rebuilds_projection(isolated_registry: Path) -> None:
    log = isolated_registry / "registry.jsonl"
    store.append_log("attachment_observed", {"registration_id": _RID, "chat_url": _CSE})
    with store.ports_lock():
        proj.project_fold_tail()
    log.write_text("")
    store.append_log("standdown_unreachable", {"registration_id": _RID})
    with store.ports_lock():
        receipt = proj.project_fold_tail()
    assert receipt.rebuilt
    rows = [
        json.loads(r)["event"] for r in proj.projection_path().read_text().splitlines()
    ]
    assert rows == ["standdown_unreachable"]
