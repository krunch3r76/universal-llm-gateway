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
        prompt_uri=("cortex://notes/system/ephemeral/cdp-endpoint/from-uri/prompt.md"),
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


def _hermetic_operator_proxy_resolve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "claude_bundles.operator_proxy_live_leg._default_fold_state_path",
        lambda: tmp_path / "live-leg-fold-state.json",
    )
    monkeypatch.setattr(
        "claude_bundles.operator_proxy_live_leg.fetch_turns_to_closure",
        lambda *_args, **_kwargs: ([], False),
    )
    monkeypatch.setattr(
        "claude_bundles.operator_proxy_live_leg.fetch_lane_status",
        lambda _lane: None,
    )


def test_resolve_prompt_mission_fallback_prepends_arrival_skills_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Runner path when sealed prompt lacks <!--cdp-required-skills:…-->."""
    from claude_bundles.cowork_skill_delivery import extract_cdp_required_authority
    from claude_bundles.operator_proxy_mission import (
        MISSION_ARRIVAL_SKILL_SLUGS,
        MISSION_TRIGGER_SKILL_SLUGS,
    )

    from cdp_ask.runner import resolve_prompt

    _hermetic_operator_proxy_resolve(monkeypatch, tmp_path)
    req = SubmitProjectAskRequest(
        prompt_text="thread_id: 9501\n# Mission\nDo the thing.\n",
        purpose="operator-proxy",
    )
    with patch(
        "claude_bundles.operator_proxy_hop_status.standing_handoff_path"
    ) as path:
        path.return_value.is_file.return_value = False
        rendered = resolve_prompt(req)[0]
    authority = extract_cdp_required_authority(rendered)
    assert authority is not None
    sealed = {s.lstrip("/").lower() for s in authority}
    for slug in MISSION_ARRIVAL_SKILL_SLUGS:
        assert slug.lower() in sealed
    for slug in MISSION_TRIGGER_SKILL_SLUGS:
        assert slug.lower() not in sealed


def test_resolve_prompt_puts_stargate_execution_id_on_the_birth_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cdp_ask.runner import resolve_prompt

    _hermetic_operator_proxy_resolve(monkeypatch, tmp_path)
    req = SubmitProjectAskRequest(
        prompt_text="thread_id: 9501\n# Mission\n",
        purpose="operator-proxy",
        stargate_execution_id="sg-birth-1",
    )
    with patch(
        "claude_bundles.operator_proxy_hop_status.standing_handoff_path"
    ) as path:
        path.return_value.is_file.return_value = False
        rendered = resolve_prompt(req)[0]
    assert "execution_id sg-birth-1" in rendered
    assert "CDP generate" not in rendered
    assert "Author the standing handoff before you leave." in rendered


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


def test_default_archive_path_code_surface_ignores_project_uuid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    exec_id = "e" * 32
    req = SubmitProjectAskRequest(
        converse=True,
        project_uuid="p",
        claude_surface="code",
        model="opus-5",
    )
    path = default_archive_path(req, execution_id=exec_id)
    assert path.endswith(f"/cdp-ask-archive-cdp-opus-{exec_id}.md")
    assert "-p-" not in path


def test_default_archive_path_cowork_strips_project_uuid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    exec_id = "f" * 32
    req = SubmitProjectAskRequest(
        converse=True,
        project_uuid=" p ",
        claude_surface="cowork",
        model="opus-5",
    )
    path = default_archive_path(req, execution_id=exec_id)
    assert path.endswith(f"/cdp-ask-archive-cdp-opus-p-{exec_id}.md")


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


@pytest.mark.asyncio
async def test_converse_archives_failed_result_with_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T7 — non-ok converse harvest with body is archived as failed."""
    root = tmp_path / "files"
    root.mkdir(parents=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    reg = MagicMock()
    reg.registration_id = "reg-fail-archive"
    reg.cdp_url = "http://127.0.0.1:9222"
    body = "X" * 9000
    err = "skill_receipt_unverified: wrong_channel=['reasoning-posture']"
    fail = ProjectAskResult(
        ok=False,
        body=body,
        url="https://claude.ai/code/cse_fail",
        project_uuid="",
        project_url="https://claude.ai/code",
        model={"ok": True},
        body_len=len(body),
        delete_after={"deleted": False},
        error=err,
        archive_uri=None,
        attested_model="Model: Opus 5",
        harvest_provenance="chat",
    )
    exec_id = "f" * 32
    with (
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(return_value=[fail]),
        ),
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
                claude_surface="code",
            ),
            execution_id=exec_id,
            abort_check=AsyncMock(return_value=False),
        )

    assert payload["ok"] is False
    assert payload["stall_stage"] == "observer_unverified"
    assert payload["archive_uri"]
    uri = payload["archive_uri"]
    if uri.startswith("file://"):
        full_path = Path(uri.removeprefix("file://"))
    else:
        full_path = root / uri.removeprefix("cortex://")
    text = full_path.read_text(encoding="utf-8")
    assert "- outcome: failed" in text
    assert "- error:" in text and "skill_receipt_unverified" in text
    assert body in text


@pytest.mark.asyncio
async def test_failed_converse_archive_write_error_keeps_original_error() -> None:
    """S2 — HarvestArchiveError on a failed run must not mask converse error."""
    from claude_bundles.project_ask import HarvestArchiveError

    reg = MagicMock()
    reg.registration_id = "reg-archive-mask"
    reg.cdp_url = "http://127.0.0.1:9222"
    cause = "skill_receipt_unverified: undelivered=['reasoning-posture']"
    fail = ProjectAskResult(
        ok=False,
        body="Y" * 9000,
        url="https://claude.ai/code/cse_mask",
        project_uuid="",
        project_url="https://claude.ai/code",
        model={"ok": True},
        body_len=9000,
        delete_after={"deleted": False},
        error=cause,
        archive_uri=None,
        attested_model=None,
        harvest_provenance="chat",
    )
    with (
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(return_value=[fail]),
        ),
        patch(
            "cdp_ask.runner.archive_harvest",
            side_effect=HarvestArchiveError("sha256 mismatch"),
        ),
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
                claude_surface="code",
            ),
            execution_id="c" * 32,
            abort_check=AsyncMock(return_value=False),
        )

    assert payload["ok"] is False
    assert payload["error"] == cause
    assert "archive_error" not in payload
    from cdp_ask.unverifiable import converse_stall_stage

    assert payload["stall_stage"] == converse_stall_stage(cause, conv_ok=False)


@pytest.mark.asyncio
async def test_success_converse_archive_write_error_surfaces_archive_exc() -> None:
    """S2 — HarvestArchiveError on a successful run must not mask harvest ok."""
    from claude_bundles.project_ask import HarvestArchiveError

    reg = MagicMock()
    reg.registration_id = "reg-archive-ok-fail"
    reg.cdp_url = "http://127.0.0.1:9222"
    ok_result = ProjectAskResult(
        ok=True,
        body="X" * 9000,
        url="https://claude.ai/code/cse_ok",
        project_uuid="",
        project_url="https://claude.ai/code",
        model={"ok": True},
        body_len=9000,
        delete_after={"deleted": False},
        error=None,
        archive_uri=None,
        attested_model=None,
        harvest_provenance="chat",
    )
    with (
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(return_value=[ok_result]),
        ),
        patch(
            "cdp_ask.runner.archive_harvest",
            side_effect=HarvestArchiveError("disk full"),
        ),
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
                claude_surface="code",
            ),
            execution_id="d" * 32,
            abort_check=AsyncMock(return_value=False),
        )

    assert payload["ok"] is False
    assert payload["error"] == "disk full"
    assert "archive_error" not in payload


@pytest.mark.asyncio
async def test_failed_converse_does_not_pass_archive_to_content_proof() -> None:
    """B2 — failed converse must not stamp content_proof via archived failure."""
    reg = MagicMock()
    reg.registration_id = "reg-no-proof"
    reg.cdp_url = "http://127.0.0.1:9222"
    fail = ProjectAskResult(
        ok=False,
        body="Z" * 9000,
        url="https://claude.ai/code/cse_noproof",
        project_uuid="",
        project_url="https://claude.ai/code",
        model={"ok": True},
        body_len=9000,
        delete_after={"deleted": False},
        error="skill_receipt_unverified: x",
        archive_uri=None,
        attested_model=None,
        harvest_provenance="chat",
    )
    release = AsyncMock()
    with (
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(return_value=[fail]),
        ),
        patch("cdp_ask.runner.deregister_on_exit"),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("cdp_ask.runner.cdp_registry.bind_session_address"),
        patch("cdp_ask.runner._wake_debt_extras", return_value={}),
        patch("cdp_ask.runner._release_f6_and_advance_proof", release),
    ):
        payload = await run_execution(
            SubmitProjectAskRequest(
                prompt_text="ping",
                converse=True,
                no_project_uuid=True,
                purpose="review",
                claude_surface="code",
            ),
            execution_id="d" * 32,
            abort_check=AsyncMock(return_value=False),
        )

    assert payload["ok"] is False
    assert payload.get("archive_uri")
    release.assert_awaited()
    assert release.await_args.kwargs.get("archive_uri") is None


@pytest.mark.asyncio
async def test_converse_skips_archive_when_failed_body_empty() -> None:
    """T7 — non-ok with empty body does not write an archive."""
    reg = MagicMock()
    reg.registration_id = "reg-fail-empty"
    reg.cdp_url = "http://127.0.0.1:9222"
    fail = ProjectAskResult(
        ok=False,
        body="",
        url="https://claude.ai/code/cse_fail",
        project_uuid="",
        project_url="https://claude.ai/code",
        model={},
        body_len=0,
        delete_after={"deleted": False},
        error="skill_receipt_unverified: empty",
        archive_uri=None,
        attested_model=None,
        harvest_provenance=None,
    )
    with (
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(return_value=[fail]),
        ),
        patch("cdp_ask.runner.archive_harvest") as archive_mock,
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
            execution_id="e" * 32,
            abort_check=AsyncMock(return_value=False),
        )
    archive_mock.assert_not_called()
    assert payload.get("archive_uri") is None
