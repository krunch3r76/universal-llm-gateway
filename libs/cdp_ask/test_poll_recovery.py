"""Tests for execution-store-miss poll recovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.poll_recovery import (
    correlation_tokens,
    recover_poll_snapshot,
    snapshot_from_archive_token,
    stargate_id_from_satellite,
)

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
    assert "grade_trace" not in snap
    assert set(snap) == {
        "execution_id",
        "status",
        "ok",
        "archive_uri",
        "body",
        "body_len",
        "url",
        "attested_model",
        "harvest_provenance",
        "completion_phase",
    }


def test_snapshot_from_archive_caps_banner_trace(tmp_path: Path) -> None:
    """A hand-edited archive cannot push an uncapped banner through the rebuild."""
    exe = "68a8129ca2264fe088ec267a20d88376"
    archive = tmp_path / f"cdp-ask-archive-{exe}.md"
    archive.write_text(
        "# CDP ask harvest\n\n"
        f"- execution_id: `{exe}`\n"
        "- url: `https://claude.ai/cowork/cse_capBanner1`\n"
        f"- error_banner_text: `{'weekly limit ' * 80}`\n"
        f"- error_banner_match: `{'m' * 200}`\n"
        "\n## Body\n\nseat answer with proof\n",
        encoding="utf-8",
    )
    snap = snapshot_from_archive_token(exe, archive_dir=tmp_path)
    assert snap is not None
    assert len(snap["grade_trace"]["banner_text"]) == 500
    assert len(snap["grade_trace"]["banner_match"]) == 120
    assert "\n" not in snap["grade_trace"]["banner_text"]


def test_recovered_grade_trace_survives_poll_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP poll path keeps grade_trace that ExecutionPollResponse used to drop."""
    from fastapi.testclient import TestClient

    from cdp_ask.app import create_app

    exe = "b" * 32
    snap = {
        "execution_id": exe,
        "status": "completed",
        "ok": True,
        "completion_phase": "terminal",
        "body": "seat answer",
        "grade_trace": {"archived_banner_text": "You've hit your weekly limit"},
    }

    async def _recover(*_args, **_kwargs):
        return snap

    monkeypatch.setattr("cdp_ask.poll_recovery.recover_poll_snapshot", _recover)
    client = TestClient(create_app())
    response = client.get(f"/v1/project-ask/executions/{exe}")
    assert response.status_code == 200
    assert response.json()["grade_trace"]["archived_banner_text"].startswith("You've")


def test_snapshot_from_archive_token_rejects_chrome_only(tmp_path: Path) -> None:
    from chat_harvest.test_chrome import SPECIMEN_346_BODY

    exe = "93f3d511a30e4de08a1de65bf6320c0e"
    archive = tmp_path / f"cdp-ask-archive-cdp-recover-{exe}.md"
    archive.write_text(
        "# CDP ask harvest\n\n"
        f"- execution_id: `{exe}`\n"
        "- url: `https://claude.ai/cowork/cse_chromeOnly`\n"
        f"\n## Body\n\n{SPECIMEN_346_BODY}\n",
        encoding="utf-8",
    )
    assert snapshot_from_archive_token(exe, archive_dir=tmp_path) is None


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


def _no_satellite_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.snapshot_from_archive_token",
        lambda _token, archive_dir=None: None,
    )
    monkeypatch.setattr("cdp_ask.poll_recovery.chat_url_from_archives", lambda _t: None)
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.chat_url_from_provenance", lambda _t: None
    )
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.resolve_harvest_chat_url",
        AsyncMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_recover_poll_snapshot_uses_caller_chat_url_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """a:36568 — registry/provenance lost the URL; the poller's hint recovers."""
    _no_satellite_url(monkeypatch)
    cse = "https://claude.ai/cowork/cse_01SM31isUQXVUvEN7S2i9uT4"
    harvest = AsyncMock(return_value={"status": "completed", "url": cse})
    monkeypatch.setattr("cdp_ask.poll_recovery._harvest_chat_to_snapshot", harvest)
    exe = "30c0e62c78a24891ad54dcee2f6bc809"
    assert await recover_poll_snapshot(exe, ExecutionStore()) is None
    harvest.assert_not_awaited()
    monkeypatch.setattr("cdp_ask.poll_recovery._recovery_last_attempt", {})
    snap = await recover_poll_snapshot(exe, ExecutionStore(), chat_url_hint=cse)
    assert snap == {"status": "completed", "url": cse}
    assert harvest.await_args.kwargs["chat_url"] == cse


@pytest.mark.asyncio
async def test_recover_poll_snapshot_rejects_non_cse_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_satellite_url(monkeypatch)
    harvest = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr("cdp_ask.poll_recovery._harvest_chat_to_snapshot", harvest)
    for hint in ("https://claude.ai/chat/abc", "https://evil.test/cowork/cse_x"):
        assert (
            await recover_poll_snapshot(
                "40c0e62c78a24891ad54dcee2f6bc809",
                ExecutionStore(),
                chat_url_hint=hint,
            )
            is None
        )
    harvest.assert_not_awaited()


def test_stargate_id_from_satellite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
