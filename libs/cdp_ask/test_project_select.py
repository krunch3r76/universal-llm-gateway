"""Hermetic tests for claude.ai Project name selection before compose submit."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.models import SubmitProjectAskRequest
from claude_bundles.project_chrome import (
    ClaudeProjectNotFoundError,
    _projects_from_list_payload,
    resolve_project_uuid_by_name,
)

from cdp_ask.project_select import (
    DEFAULT_CLAUDE_PROJECT_NAME,
    ClaudeProjectSelectError,
    apply_project_select,
    project_select_needed,
)

pytestmark = [pytest.mark.offline, pytest.mark.project_select]


def test_submit_model_default_claude_project_name_is_test() -> None:
    req = SubmitProjectAskRequest(prompt_text="hello")
    assert req.claude_project_name == DEFAULT_CLAUDE_PROJECT_NAME


def test_project_select_needed_skips_explicit_uuid() -> None:
    req = SubmitProjectAskRequest(prompt_text="x", project_uuid="uuid-1")
    assert project_select_needed(req) is False


def test_project_select_needed_skips_code_surface() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="x",
        converse=True,
        no_project_uuid=True,
        claude_surface="code",
    )
    assert project_select_needed(req) is False


@pytest.mark.asyncio
async def test_apply_project_select_default_test() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
    )
    with patch(
        "cdp_ask.project_select.connect_cdp",
        new=AsyncMock(return_value=(MagicMock(), None, None, MagicMock())),
    ) as connect:
        connect.return_value[0].stop = AsyncMock()
        with patch(
            "cdp_ask.project_select.resolve_project_uuid_by_name",
            new=AsyncMock(return_value="proj-test-uuid"),
        ) as resolve:
            out = await apply_project_select(req, cdp_url="http://127.0.0.1:9226")
    resolve.assert_awaited_once()
    assert resolve.await_args.args[1] == "test"
    assert out.project_uuid == "proj-test-uuid"
    assert out.no_project_uuid is False


@pytest.mark.asyncio
async def test_apply_project_select_override_name() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        claude_project_name="staging",
    )
    with patch(
        "cdp_ask.project_select.connect_cdp",
        new=AsyncMock(return_value=(MagicMock(), None, None, MagicMock())),
    ) as connect:
        connect.return_value[0].stop = AsyncMock()
        with patch(
            "cdp_ask.project_select.resolve_project_uuid_by_name",
            new=AsyncMock(return_value="proj-staging-uuid"),
        ) as resolve:
            out = await apply_project_select(req, cdp_url="http://127.0.0.1:9226")
    resolve.assert_awaited_once()
    assert resolve.await_args.args[1] == "staging"
    assert out.project_uuid == "proj-staging-uuid"
    assert out.claude_project_name == "staging"


@pytest.mark.asyncio
async def test_apply_project_select_not_found_fail_closed() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        claude_project_name="missing",
    )
    with patch(
        "cdp_ask.project_select.connect_cdp",
        new=AsyncMock(return_value=(MagicMock(), None, None, MagicMock())),
    ) as connect:
        connect.return_value[0].stop = AsyncMock()
        with patch(
            "cdp_ask.project_select.resolve_project_uuid_by_name",
            new=AsyncMock(
                side_effect=ClaudeProjectNotFoundError(
                    "claude project not found: 'missing'"
                )
            ),
        ) as resolve:
            with pytest.raises(ClaudeProjectSelectError, match="not found"):
                await apply_project_select(req, cdp_url="http://127.0.0.1:9226")
    connect.return_value[0].stop.assert_awaited_once()
    resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_project_select_blank_name_fail_closed() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        claude_project_name="",
    )
    with pytest.raises(ClaudeProjectSelectError, match="required"):
        await apply_project_select(req, cdp_url="http://127.0.0.1:9226")


@pytest.mark.asyncio
async def test_apply_project_select_connect_failure_wrapped() -> None:
    req = SubmitProjectAskRequest(prompt_text="probe", converse=True)
    with patch(
        "cdp_ask.project_select.connect_cdp",
        new=AsyncMock(side_effect=RuntimeError("no browser")),
    ):
        with pytest.raises(ClaudeProjectSelectError, match="cdp connect"):
            await apply_project_select(req, cdp_url="http://127.0.0.1:9226")


@pytest.mark.asyncio
async def test_apply_project_select_list_failure_wrapped() -> None:
    req = SubmitProjectAskRequest(prompt_text="probe", converse=True)
    pw = MagicMock()
    pw.stop = AsyncMock()
    with patch(
        "cdp_ask.project_select.connect_cdp",
        new=AsyncMock(return_value=(pw, None, None, MagicMock())),
    ):
        with patch(
            "cdp_ask.project_select.resolve_project_uuid_by_name",
            new=AsyncMock(side_effect=RuntimeError("list projects failed")),
        ):
            with pytest.raises(ClaudeProjectSelectError, match="list projects"):
                await apply_project_select(req, cdp_url="http://127.0.0.1:9226")
    pw.stop.assert_awaited_once()


def test_projects_from_list_payload_shapes() -> None:
    row = {"uuid": "u1", "name": "test"}
    assert _projects_from_list_payload([row]) == [row]
    assert _projects_from_list_payload({"data": [row]}) == [row]
    assert _projects_from_list_payload({"projects": [row]}) == [row]
    assert _projects_from_list_payload({"items": [row]}) == [row]
    assert _projects_from_list_payload({"results": [row]}) == [row]
    assert _projects_from_list_payload({}) == []


@pytest.mark.asyncio
async def test_resolve_project_uuid_by_name_happy() -> None:
    page = MagicMock()
    rows = [{"uuid": "uuid-a", "name": "test"}]
    with (
        patch(
            "claude_bundles.project_chrome.ensure_claude_origin",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_chrome.list_organization_projects",
            new=AsyncMock(return_value=rows),
        ),
    ):
        assert await resolve_project_uuid_by_name(page, "test") == "uuid-a"


@pytest.mark.asyncio
async def test_resolve_project_uuid_by_name_missing() -> None:
    page = MagicMock()
    with (
        patch(
            "claude_bundles.project_chrome.ensure_claude_origin",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_chrome.list_organization_projects",
            new=AsyncMock(return_value=[]),
        ),
    ):
        with pytest.raises(ClaudeProjectNotFoundError, match="not found"):
            await resolve_project_uuid_by_name(page, "nope")


@pytest.mark.asyncio
async def test_resolve_project_uuid_by_name_ambiguous() -> None:
    page = MagicMock()
    rows = [
        {"uuid": "u1", "name": "dup"},
        {"uuid": "u2", "name": "dup"},
    ]
    with (
        patch(
            "claude_bundles.project_chrome.ensure_claude_origin",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_chrome.list_organization_projects",
            new=AsyncMock(return_value=rows),
        ),
    ):
        with pytest.raises(ClaudeProjectNotFoundError, match="ambiguous"):
            await resolve_project_uuid_by_name(page, "dup")


@pytest.mark.asyncio
async def test_resolve_project_uuid_by_name_blank() -> None:
    with pytest.raises(ClaudeProjectNotFoundError, match="required"):
        await resolve_project_uuid_by_name(MagicMock(), "   ")


@pytest.mark.asyncio
async def test_resolve_project_uuid_by_name_skips_incomplete_rows() -> None:
    page = MagicMock()
    rows = [
        {"uuid": "", "name": "test"},
        {"name": "orphan"},
        {"uuid": "good", "name": ""},
        {"uuid": "uuid-ok", "name": "test"},
    ]
    with (
        patch(
            "claude_bundles.project_chrome.ensure_claude_origin",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_chrome.list_organization_projects",
            new=AsyncMock(return_value=rows),
        ),
    ):
        assert await resolve_project_uuid_by_name(page, "test") == "uuid-ok"


@pytest.mark.asyncio
async def test_run_execution_selects_before_conversation() -> None:
    from cdp_ask.runner import run_execution

    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        claude_project_name="test",
        model="opus-5",
    )
    reg = MagicMock()
    reg.registration_id = "reg-test"
    reg.cdp_url = "http://127.0.0.1:9226"
    reg.target_id = None

    ok_result = MagicMock(
        ok=True,
        body="ok",
        url="https://claude.ai/cowork/cse_x",
        project_uuid="proj-test-uuid",
        project_url="https://claude.ai/cowork/project/proj-test-uuid",
        model={},
        body_len=2,
        delete_after=False,
        error=None,
        archive_uri="cortex://x",
        attested_model=None,
        harvest_provenance=None,
    )

    async def _converse(*_args, **_kwargs):
        return [ok_result]

    converse = AsyncMock(side_effect=_converse)

    async def _overlay(*, run_once, **_kw):
        return await run_once(req.model), {}

    selected = req.model_copy(
        update={"project_uuid": "proj-test-uuid", "no_project_uuid": False}
    )

    with (
        patch("cdp_ask.runner.resolve_prompt", return_value=["probe"]),
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.apply_project_select",
            new=AsyncMock(return_value=selected),
        ),
        patch(
            "cdp_ask.runner.run_with_overlay_retry", new=AsyncMock(side_effect=_overlay)
        ),
        patch("cdp_ask.runner.run_project_conversation", new=converse),
        patch("cdp_ask.runner.archive_harvest", return_value="cortex://x"),
        patch("cdp_ask.runner._persist_session_address"),
    ):
        result = await run_execution(req, execution_id="exec-1", abort_check=AsyncMock(return_value=False))

    assert result["ok"] is True
    converse.assert_awaited_once()
    assert converse.await_args.kwargs["project_uuid"] == "proj-test-uuid"


@pytest.mark.asyncio
async def test_run_execution_select_failure_releases_lane() -> None:
    from cdp_ask.runner import run_execution

    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        model="opus-5",
    )
    reg = MagicMock()
    reg.registration_id = "reg-select-fail"
    reg.cdp_url = "http://127.0.0.1:9226"
    reg.target_id = None

    converse = AsyncMock()
    ask = AsyncMock()
    deregister = MagicMock()
    select = AsyncMock(side_effect=ClaudeProjectSelectError("claude project not found"))

    with (
        patch("cdp_ask.runner.resolve_prompt", return_value=["probe"]),
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch("cdp_ask.runner.apply_project_select", new=select),
        patch("cdp_ask.runner.run_project_conversation", new=converse),
        patch("cdp_ask.runner.run_project_ask", new=ask),
        patch("cdp_ask.runner.deregister_on_exit", deregister),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("cdp_ask.runner.cdp_registry._load_active", return_value={}),
    ):
        with pytest.raises(ClaudeProjectSelectError):
            await run_execution(
                req,
                execution_id="exec-select-fail",
                abort_check=AsyncMock(return_value=False),
            )

    select.assert_awaited_once()
    converse.assert_not_awaited()
    ask.assert_not_awaited()
    deregister.assert_called_once()
