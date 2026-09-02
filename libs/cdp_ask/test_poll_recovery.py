"""Tests for execution-store-miss poll recovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cdp_ask.poll_recovery import (
    correlation_tokens,
    recover_poll_snapshot,
    snapshot_from_archive_token,
    stargate_id_from_satellite,
)
from cdp_ask.execution_store import ExecutionStore

pytestmark = pytest.mark.offline


def test_snapshot_from_archive_token(tmp_path: Path) -> None:
    exe = "68a8129ca2264fe088ec267a20d88376"
    body = "## Bind record\n\nBOUND: F1 = A"
    archive = tmp_path / f"cdp-ask-archive-cdp-fable-{exe}.md"
    archive.write_text(
        "# CDP ask harvest\n\n"
        f"- execution_id: `{exe}`\n"
        "- url: `https://claude.ai/cowork/cse_testRecovery1`\n"
        "- attested_model: `Fable 5 High`\n"
        f"\n## Body\n\n{body}\n",
        encoding="utf-8",
    )
    snap = snapshot_from_archive_token(exe, archive_dir=tmp_path)
    assert snap is not None
    assert snap["status"] == "completed"
    assert snap["body"] == body
    assert snap["attested_model"] == "Fable 5 High"
    assert snap["url"].endswith("cse_testRecovery1")


@pytest.mark.asyncio
async def test_recover_poll_snapshot_prefers_archive(tmp_path: Path) -> None:
    exe = "68a8129ca2264fe088ec267a20d88376"
    (tmp_path / f"cdp-ask-archive-cdp-fable-{exe}.md").write_text(
        f"- execution_id: `{exe}`\n"
        "- url: `https://claude.ai/cowork/cse_archOnly`\n"
        "- attested_model: `Fable 5`\n"
        "\n## Body\n\nbound packet\n",
        encoding="utf-8",
    )
    store = ExecutionStore()
    with patch(
        "cdp_ask.poll_recovery._archive_dir",
        return_value=tmp_path,
    ):
        snap = await recover_poll_snapshot(exe, store)
    assert snap is not None
    assert snap["body"] == "bound packet"


@pytest.mark.asyncio
async def test_recover_poll_snapshot_harvests_when_url_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ExecutionStore()
    harvested = {
        "execution_id": "68a8129ca2264fe088ec267a20d88376",
        "status": "completed",
        "ok": True,
        "body": "recovered bind",
        "body_len": 14,
        "url": "https://claude.ai/cowork/cse_harvested",
        "attested_model": "Fable 5 High",
        "harvest_provenance": "output-file",
        "completion_phase": "terminal",
        "archive_uri": "cortex://notes/system/threads/x.md",
    }
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.snapshot_from_archive_token",
        lambda _token, archive_dir=None: None,
    )
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.chat_url_from_archives",
        lambda _token: "https://claude.ai/cowork/cse_harvested",
    )
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.chat_url_from_provenance",
        lambda _token: None,
    )
    monkeypatch.setattr(
        "cdp_ask.poll_recovery._harvest_chat_to_snapshot",
        AsyncMock(return_value=harvested),
    )
    snap = await recover_poll_snapshot("68a8129ca2264fe088ec267a20d88376", store)
    assert snap == harvested


def test_stargate_id_from_satellite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = tmp_path / "stargate-cdp-generate-inflight.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE cdp_inflight_leg ("
        "execution_id TEXT PRIMARY KEY, satellite_execution_id TEXT)"
    )
    conn.execute(
        "INSERT INTO cdp_inflight_leg VALUES (?, ?)",
        ("71c6dcaa-c6eb-4704-954f-11fe97d2ef46", "68a8129ca2264fe088ec267a20d88376"),
    )
    conn.commit()
    conn.close()
    assert stargate_id_from_satellite("68a8129ca2264fe088ec267a20d88376") == (
        "71c6dcaa-c6eb-4704-954f-11fe97d2ef46"
    )
    tokens = correlation_tokens("71c6dcaa-c6eb-4704-954f-11fe97d2ef46")
    assert tokens[0] == "68a8129ca2264fe088ec267a20d88376"
    assert "71c6dcaa-c6eb-4704-954f-11fe97d2ef46" in tokens
