"""Offline tests for CDP ask archive path identity (a:25137 / F2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles.project_ask import ProjectAskResult

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import (
    default_archive_path,
    resolve_stargate_execution_id,
    run_execution,
)

pytestmark = pytest.mark.offline


def test_resolve_stargate_execution_id_prefers_explicit_field() -> None:
    req = SubmitProjectAskRequest(
        prompt_uri=(
            "cortex://notes/system/ephemeral/cdp-endpoint/from-uri/prompt.md"
        ),
        stargate_execution_id="from-field",
    )
    assert resolve_stargate_execution_id(req) == "from-field"


def test_resolve_stargate_execution_id_parses_ephemeral_uri() -> None:
    req = SubmitProjectAskRequest(
        prompt_uri=(
            "cortex://notes/system/ephemeral/cdp-endpoint/sg-from-uri/prompt.md"
        ),
    )
    assert resolve_stargate_execution_id(req) == "sg-from-uri"


def test_resolve_stargate_execution_id_empty_without_carrier() -> None:
    req = SubmitProjectAskRequest(prompt_text="hello")
    assert resolve_stargate_execution_id(req) == ""


def test_default_archive_path_scopes_new_asks_by_execution_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    req = SubmitProjectAskRequest(converse=True, no_project_uuid=True, model="opus-5")
    path_a = default_archive_path(req, execution_id="a" * 32)
    path_b = default_archive_path(req, execution_id="b" * 32)
    assert path_a != path_b
    assert path_a.endswith("/cdp-ask-archive-cdp-opus-" + ("a" * 32) + ".md")
    assert path_b.endswith("/cdp-ask-archive-cdp-opus-" + ("b" * 32) + ".md")


def test_default_archive_path_full_id_survives_shared_exec8_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Terra amend (a): exec8 truncation must not collapse distinct executions."""
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    req = SubmitProjectAskRequest(converse=True, no_project_uuid=True, model="fable")
    prefix = "65b24006"
    path_a = default_archive_path(req, execution_id=prefix + ("a" * 24))
    path_b = default_archive_path(req, execution_id=prefix + ("b" * 24))
    assert path_a != path_b
    assert prefix + ("a" * 24) in path_a
    assert prefix + ("b" * 24) in path_b


def test_default_archive_path_honors_explicit_archive_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    explicit = str(root / "notes/system/threads/custom-archive.md")
    req = SubmitProjectAskRequest(
        converse=True,
        no_project_uuid=True,
        archive_path=explicit,
    )
    assert default_archive_path(req, execution_id="deadbeef" * 4) == explicit


def test_default_archive_path_scopes_project_uuid_by_execution_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    life_uuid = "01a05c28-733b-72ee-bba6-c72e81ed6d41"
    req = SubmitProjectAskRequest(
        converse=True,
        no_project_uuid=False,
        project_uuid=life_uuid,
        model="opus-5",
    )
    path_a = default_archive_path(req, execution_id="a" * 32)
    path_b = default_archive_path(req, execution_id="b" * 32)
    assert path_a != path_b
    assert life_uuid in path_a and ("a" * 32) in path_a
    assert life_uuid in path_b and ("b" * 32) in path_b


@pytest.mark.asyncio
async def test_backfill_preserves_all_fields_and_sets_archive_uri() -> None:
    """B1 — converse archive backfill forwards all 14 ProjectAskResult fields."""
    reg = MagicMock()
    reg.registration_id = "reg-backfill"
    reg.cdp_url = "http://127.0.0.1:9222"
    cards = ({"title": "Spec", "kind": "MD"},)
    converse_result = ProjectAskResult(
        ok=True,
        body="harvest body",
        url="https://claude.ai/cowork/cse_backfill",
        project_uuid="",
        project_url="https://claude.ai/new",
        model={"ok": True, "current_model": "Model: Opus 5"},
        body_len=12,
        delete_after={"deleted": False},
        error=None,
        archive_uri=None,
        attested_model="Model: Opus 5",
        harvest_provenance="chat",
        artifact_cards=cards,
        artifact_cards_unresolved=True,
    )
    archived = "cortex://notes/system/threads/cdp-ask-archive-backfill.md"
    with (
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(return_value=[converse_result]),
        ),
        patch("cdp_ask.runner.archive_harvest", return_value=archived),
        patch("cdp_ask.runner.deregister_on_exit"),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("cdp_ask.runner.cdp_registry.bind_session_address"),
        patch("cdp_ask.runner._wake_debt_extras", return_value={}),
    ):
        payload = await run_execution(
            SubmitProjectAskRequest(
                prompt_text="ping",
                converse=True,
                no_project_uuid=True,
                purpose="review",
            ),
            execution_id="exec-backfill",
            abort_check=AsyncMock(return_value=False),
        )

    assert payload["ok"] is True
    assert payload["archive_uri"] == archived
    backfilled = payload["results"][0]
    assert backfilled["archive_uri"] == archived
    assert backfilled["attested_model"] == "Model: Opus 5"
    assert backfilled["artifact_cards"] == cards
    assert backfilled["artifact_cards_unresolved"] is True
    assert backfilled["harvest_provenance"] == "chat"
    assert backfilled["delete_after"] == {"deleted": False}
