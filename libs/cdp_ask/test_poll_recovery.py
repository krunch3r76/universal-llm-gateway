"""Tests for execution-store-miss poll recovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.cse_session_models import HarvestResponse
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.poll_recovery import (
    RECOVERED_UNVERIFIED_DOM_ERROR,
    _harvest_chat_to_snapshot,
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


def test_snapshot_from_archive_failed_outcome(tmp_path: Path) -> None:
    """Failed harvest archives rebuild as ok=False (store-row miss recovery)."""
    exe = "f" * 32
    body = "X" * 200
    err = "converse turn failed: undelivered=['reasoning-posture']"
    archive = tmp_path / f"cdp-ask-archive-cdp-code-{exe}.md"
    archive.write_text(
        "# CDP ask harvest\n\n"
        f"- execution_id: `{exe}`\n"
        "- url: `https://claude.ai/code/cse_fail`\n"
        f"- outcome: failed\n"
        f"- error: {err}\n"
        f"\n## Body\n\n{body}\n",
        encoding="utf-8",
    )
    snap = snapshot_from_archive_token(exe, archive_dir=tmp_path)
    assert snap is not None
    assert snap["ok"] is False
    assert snap["status"] == "failed"
    assert snap["error"] == err
    assert snap["body"] == body
    assert snap["archive_uri"]
    assert snap["harvest_provenance"] is None


@pytest.mark.asyncio
async def test_recover_poll_snapshot_failed_archive_no_store_row(
    tmp_path: Path,
) -> None:
    exe = "a" * 32
    err = "converse turn failed: empty"
    (tmp_path / f"cdp-ask-archive-seat-{exe}.md").write_text(
        "# CDP ask harvest\n\n"
        f"- execution_id: `{exe}`\n"
        "- url: `https://claude.ai/code/cse_x`\n"
        f"- outcome: failed\n"
        f"- error: {err}\n"
        "\n## Body\n\nfailed reply body here\n",
        encoding="utf-8",
    )
    store = ExecutionStore()
    with patch(
        "cdp_ask.poll_recovery._archive_dir",
        return_value=tmp_path,
    ):
        snap = await recover_poll_snapshot(exe, store)
    assert snap is not None
    assert snap["ok"] is False
    assert snap["status"] == "failed"


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Legacy routing specimen — DOM recovery is fail-closed (T2)."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    store = ExecutionStore()
    fail_closed = {
        "execution_id": "68a8129ca2264fe088ec267a20d88376",
        "status": "failed",
        "ok": False,
        "error": RECOVERED_UNVERIFIED_DOM_ERROR,
        "stall_stage": "recovered_unverified_dom",
        "harvest_provenance": None,
        "completion_phase": "terminal",
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
        AsyncMock(return_value=fail_closed),
    )
    snap = await recover_poll_snapshot("68a8129ca2264fe088ec267a20d88376", store)
    assert snap == fail_closed


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
    harvest = AsyncMock(
        return_value={
            "status": "failed",
            "ok": False,
            "url": cse,
            "stall_stage": "recovered_unverified_dom",
            "error": RECOVERED_UNVERIFIED_DOM_ERROR,
            "harvest_provenance": None,
        }
    )
    monkeypatch.setattr("cdp_ask.poll_recovery._harvest_chat_to_snapshot", harvest)
    exe = "30c0e62c78a24891ad54dcee2f6bc809"
    assert await recover_poll_snapshot(exe, ExecutionStore()) is None
    harvest.assert_not_awaited()
    monkeypatch.setattr("cdp_ask.poll_recovery._recovery_last_attempt", {})
    snap = await recover_poll_snapshot(exe, ExecutionStore(), chat_url_hint=cse)
    assert snap["ok"] is False
    assert snap["stall_stage"] == "recovered_unverified_dom"
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


@pytest.mark.asyncio
async def test_harvest_chat_to_snapshot_fail_closed_first_poll(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """T2a — unverified DOM recovery returns ok=False + recovered_unverified_dom."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    page = AsyncMock()
    harvest = HarvestResponse(outcome="harvested", turns=(), content_provenance="cse-dom")
    harvest.turns = (type("T", (), {"text": "recovered prose body " * 5})(),)

    async def _attach(*_a, **_kw):
        return type("O", (), {"ok": True, "page": page})()

    monkeypatch.setattr(
        "cdp_ask.followup_reattach.ensure_cse_attached",
        _attach,
    )
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.current_model_label",
        AsyncMock(return_value="Opus 5"),
    )
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.harvest_page",
        AsyncMock(return_value=harvest),
    )
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_open._teardown_opened",
        AsyncMock(),
    )
    snap = await _harvest_chat_to_snapshot(
        chat_url="https://claude.ai/cowork/cse_t2a",
        satellite_id="b" * 32,
        stargate_execution_id="s" * 32,
    )
    assert snap is not None
    assert snap["ok"] is False
    assert snap["stall_stage"] == "recovered_unverified_dom"
    assert snap["error"] == RECOVERED_UNVERIFIED_DOM_ERROR
    assert snap.get("harvest_provenance") is None


@pytest.mark.asyncio
async def test_harvest_chat_to_snapshot_archive_replay_second_poll(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """T2b — forensic archive replays failed with harvest_provenance null."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "cdp_ask.poll_recovery._archive_dir",
        lambda: tmp_path,
    )
    exe = "c" * 32
    page = AsyncMock()
    harvest = HarvestResponse(outcome="harvested", turns=(), content_provenance="cse-dom")
    harvest.turns = (type("T", (), {"text": "forensic replay body " * 5})(),)

    async def _attach(*_a, **_kw):
        return type("O", (), {"ok": True, "page": page})()

    monkeypatch.setattr("cdp_ask.followup_reattach.ensure_cse_attached", _attach)
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.current_model_label", AsyncMock(return_value="Opus 5")
    )
    monkeypatch.setattr("cdp_ask.poll_recovery.harvest_page", AsyncMock(return_value=harvest))
    monkeypatch.setattr("cdp_ask.cse_session_harvest_open._teardown_opened", AsyncMock())

    from cdp_ask.poll_recovery import snapshot_from_archive_path
    from cdp_ask.runner import verify_harvest_root

    first = await _harvest_chat_to_snapshot(
        chat_url="https://claude.ai/cowork/cse_t2b",
        satellite_id=exe,
        stargate_execution_id=None,
    )
    assert first and first["archive_uri"]
    archive_path = (
        verify_harvest_root()
        / "notes/system/threads"
        / f"cdp-ask-archive-cdp-recover-{exe}.md"
    )
    assert archive_path.is_file()
    second = snapshot_from_archive_path(archive_path, execution_id=exe)
    assert second is not None
    assert second["ok"] is False
    assert second["stall_stage"] == "recovered_unverified_dom"
    assert second["harvest_provenance"] is None
    assert second["error"] == RECOVERED_UNVERIFIED_DOM_ERROR


@pytest.mark.asyncio
async def test_harvest_dom_recovery_http_poll_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """T2 HTTP — GET after live _harvest_chat_to_snapshot (ExecutionPollResponse)."""
    from fastapi.testclient import TestClient

    from cdp_ask.app import create_app
    from cdp_ask.execution_store import ExecutionStore

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    exe = "e" * 32
    cse = "https://claude.ai/cowork/cse_t2http"
    page = AsyncMock()
    harvest = HarvestResponse(outcome="harvested", turns=(), content_provenance="cse-dom")
    harvest.turns = (type("T", (), {"text": "http poll dom body " * 5})(),)

    async def _attach(*_a, **_kw):
        return type("O", (), {"ok": True, "page": page})()

    monkeypatch.setattr("cdp_ask.followup_reattach.ensure_cse_attached", _attach)
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.current_model_label", AsyncMock(return_value="Opus 5")
    )
    monkeypatch.setattr("cdp_ask.poll_recovery.harvest_page", AsyncMock(return_value=harvest))
    monkeypatch.setattr("cdp_ask.cse_session_harvest_open._teardown_opened", AsyncMock())

    snap = await _harvest_chat_to_snapshot(
        chat_url=cse,
        satellite_id=exe,
        stargate_execution_id=None,
    )
    assert snap and snap["ok"] is False

    store = ExecutionStore()
    client = TestClient(create_app(store=store))
    response = client.get(f"/v1/project-ask/executions/{exe}")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["stall_stage"] == "recovered_unverified_dom"
    assert body["harvest_provenance"] is None
    assert body["error"] == RECOVERED_UNVERIFIED_DOM_ERROR


@pytest.mark.asyncio
async def test_receipt_miss_harvest_http_poll_agrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-R3 HTTP — store-miss GET after converse attest miss still reports ok harvest."""
    from unittest.mock import AsyncMock, patch

    from claude_bundles.cowork_skill_delivery import SkillDeliveryError
    from fastapi.testclient import TestClient

    from cdp_ask.app import create_app
    from cdp_ask.execution_store import ExecutionStore
    from cdp_ask.models import SubmitProjectAskRequest
    from cdp_ask.runner import run_execution

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    marked = (
        "<!--cdp-required-skills:reasoning-posture-->\n"
        "Turn one after attest miss."
    )
    harvested = "converse harvest after receipt miss " + ("y" * 80)
    reg = MagicMock()
    reg.registration_id = "reg-tr3"
    reg.cdp_url = "http://127.0.0.1:9222"
    reg.target_id = None
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_tr3"
    attest_calls = 0

    def _attest_miss(*_a, **_kw):
        nonlocal attest_calls
        attest_calls += 1
        if attest_calls == 1:
            raise SkillDeliveryError("undelivered=['reasoning-posture']")
        return []

    with (
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Model: Opus 5"}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0, "n": 0, "body_len": 0}),
        ),
        patch(
            "claude_bundles.skill_induction_panel.wait_for_induction_panel",
            new=AsyncMock(
                return_value=type("P", (), {"skills": ["reasoning-posture"]})()
            ),
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.attest_delivery_channels",
            side_effect=_attest_miss,
        ),
        patch(
            "claude_bundles.composer_session_skills.require_compose_surface",
        ),
        patch(
            "claude_bundles.project_ask.find_composer",
            new=AsyncMock(return_value=AsyncMock()),
        ),
        patch(
            "claude_bundles.composer_submit.clear_composer_verified",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask._submit_composer_draft",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(
                return_value={
                    "body": harvested,
                    "n": 1,
                    "url": page.url,
                    "model_label": "Model: Opus 5",
                }
            ),
        ),
        patch(
            "claude_bundles.project_ask_conversation.resolve_harvest_body",
            new=AsyncMock(
                return_value=type(
                    "H", (), {"content": harvested, "provenance": "chat"}
                )()
            ),
        ),
        patch(
            "claude_bundles.project_ask_conversation._attest_model",
            return_value="Model: Opus 5",
        ),
        patch("cdp_ask.runner.deregister_on_exit"),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("cdp_ask.runner.cdp_registry.bind_session_address"),
        patch("cdp_ask.runner._wake_debt_extras", return_value={}),
        patch.object(page, "wait_for_timeout", new=AsyncMock()),
    ):
        exec_id = "f" * 32
        payload = await run_execution(
            SubmitProjectAskRequest(
                prompt_text=marked,
                converse=True,
                no_project_uuid=True,
                delete_after=True,
            ),
            execution_id=exec_id,
            abort_check=AsyncMock(return_value=False),
        )

    assert payload["ok"] is True
    assert payload.get("error") is None
    assert payload.get("stall_stage") is None
    assert payload.get("harvest_provenance") == "chat"
    assert payload.get("archive_uri")

    store = ExecutionStore()
    record = await store.create(holder="tr3", purpose="ask")
    await store.mark_terminal(
        record.execution_id,
        status="completed",
        result=payload,
        stall_stage=payload.get("stall_stage"),
    )
    client = TestClient(create_app(store=store))
    poll = client.get(f"/v1/project-ask/executions/{record.execution_id}")
    assert poll.status_code == 200
    data = poll.json()
    assert data["ok"] is True
    assert data["error"] is None
    assert data["harvest_provenance"] == "chat"
    assert data["archive_uri"]
    assert data["stall_stage"] is None
    assert "skill_receipt_unverified" not in (data.get("error") or "")


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
