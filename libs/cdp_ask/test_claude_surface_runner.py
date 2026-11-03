"""Offline tests for claude_surface compose routing on CDP submit."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import _compose_args_for_surface, run_execution

pytestmark = pytest.mark.offline


def test_compose_args_code_surface() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="hi",
        converse=True,
        no_project_uuid=True,
        claude_surface="code",
        ensure_cowork_auto=True,
    )
    url, ensure = _compose_args_for_surface(req)
    assert url == "https://claude.ai/code"
    assert ensure is False


def test_compose_args_cowork_default() -> None:
    req = SubmitProjectAskRequest(prompt_text="hi", ensure_cowork_auto=True)
    url, ensure = _compose_args_for_surface(req)
    assert url is None
    assert ensure is True


@pytest.mark.asyncio
async def test_run_execution_passes_code_compose_to_conversation() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        claude_surface="code",
        model="opus-5",
    )
    reg = MagicMock()
    reg.registration_id = "reg-test"
    reg.cdp_url = "http://127.0.0.1:9226"
    reg.target_id = None

    ok_result = MagicMock(
        ok=True,
        body="ok",
        url="https://claude.ai/code/session_testid",
        project_uuid="",
        project_url="https://claude.ai/code",
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

    with (
        patch("cdp_ask.runner.resolve_prompt", return_value=["probe"]),
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.run_with_overlay_retry", new=AsyncMock(side_effect=_overlay)
        ),
        patch("cdp_ask.runner.run_project_conversation", new=converse),
        patch("cdp_ask.runner.archive_harvest", return_value="cortex://x"),
    ):
        result = await run_execution(
            req,
            execution_id="exec-test",
            abort_check=AsyncMock(return_value=False),
        )

    converse.assert_awaited_once()
    kwargs = converse.await_args.kwargs
    assert kwargs["compose_url"] == "https://claude.ai/code"
    assert kwargs["ensure_cowork_auto"] is False
    assert kwargs["claude_surface"] == "code"
    assert result["url"] == "https://claude.ai/code/session_testid"


def _converse_ok_result() -> MagicMock:
    return MagicMock(
        ok=True,
        body="ok",
        url="https://claude.ai/code/session_testid",
        project_uuid="",
        project_url="https://claude.ai/code",
        model={},
        body_len=2,
        delete_after=False,
        error=None,
        archive_uri="cortex://x",
        attested_model=None,
        harvest_provenance=None,
    )


@contextlib.contextmanager
def _run_execution_patches(reg: MagicMock, *, converse: AsyncMock, overlay: AsyncMock):
    patches = (
        patch("cdp_ask.runner.resolve_prompt", return_value=["probe"]),
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch("cdp_ask.runner.run_with_overlay_retry", new=overlay),
        patch("cdp_ask.runner.run_project_conversation", new=converse),
        patch("cdp_ask.runner.archive_harvest", return_value="cortex://x"),
        patch("cdp_ask.runner.deregister_on_exit"),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("cdp_ask.runner.cdp_registry.bind_session_address"),
        patch("cdp_ask.runner._wake_debt_extras", return_value={}),
    )
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


@pytest.mark.asyncio
async def test_run_execution_code_surface_drops_project_uuid_converse_true() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        project_uuid="p",
        claude_surface="code",
        model="opus-5",
    )
    reg = MagicMock(registration_id="reg-c", cdp_url="http://127.0.0.1:9226", target_id=None)
    converse = AsyncMock(return_value=[_converse_ok_result()])

    async def _overlay(*, run_once, **_kw):
        return await run_once(req.model), {}

    with _run_execution_patches(reg, converse=converse, overlay=AsyncMock(side_effect=_overlay)):
        result = await run_execution(
            req,
            execution_id="exec-conv-code",
            abort_check=AsyncMock(return_value=False),
        )

    kwargs = converse.await_args.kwargs
    assert kwargs["project_uuid"] == ""
    assert kwargs["compose_url"] == "https://claude.ai/code"
    assert kwargs["claude_surface"] == "code"
    assert result["project_uuid_ignored_for_code_surface"] is True


@pytest.mark.asyncio
async def test_run_execution_code_surface_drops_project_uuid_converse_false() -> None:
    """Non-converse + code + project_uuid routes through converse no-project /code."""
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=False,
        project_uuid="p",
        claude_surface="code",
        model="opus-5",
        delete_after=False,
    )
    reg = MagicMock(registration_id="reg-nc", cdp_url="http://127.0.0.1:9226", target_id=None)
    converse = AsyncMock(return_value=[_converse_ok_result()])
    run_ask = AsyncMock()

    async def _overlay(*, run_once, **_kw):
        return await run_once(req.model), {}

    with (
        _run_execution_patches(reg, converse=converse, overlay=AsyncMock(side_effect=_overlay)),
        patch("cdp_ask.runner.run_project_ask", new=run_ask),
    ):
        result = await run_execution(
            req,
            execution_id="exec-nc-code",
            abort_check=AsyncMock(return_value=False),
        )

    run_ask.assert_not_awaited()
    converse.assert_awaited_once()
    kwargs = converse.await_args.kwargs
    assert kwargs["project_uuid"] == ""
    assert kwargs["compose_url"] == "https://claude.ai/code"
    assert result["project_uuid_ignored_for_code_surface"] is True


@pytest.mark.asyncio
async def test_run_execution_non_converse_overlay_allows_family_substitution_for_cowork() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=False,
        project_uuid="proj-only",
        claude_surface="cowork",
        model="opus-5",
        delete_after=False,
    )
    reg = MagicMock(registration_id="reg-cw", cdp_url="http://127.0.0.1:9226", target_id=None)
    overlay = AsyncMock(
        return_value=(
            MagicMock(
                ok=True,
                error=None,
                url="",
                body="",
                body_len=0,
                project_uuid="proj-only",
                project_url="",
                model={},
                delete_after=False,
                archive_uri=None,
                attested_model=None,
                harvest_provenance=None,
            ),
            {},
        )
    )

    with (
        patch("cdp_ask.runner.resolve_prompt", return_value=["probe"]),
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch("cdp_ask.runner.run_with_overlay_retry", new=overlay),
        patch(
            "cdp_ask.runner.run_project_ask",
            new=AsyncMock(
                return_value=MagicMock(
                    ok=True,
                    error=None,
                    url="",
                    body="",
                    body_len=0,
                    project_uuid="proj-only",
                    project_url="",
                    model={},
                    delete_after=False,
                    archive_uri=None,
                    attested_model=None,
                    harvest_provenance=None,
                )
            ),
        ),
        patch("cdp_ask.runner.deregister_on_exit"),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("cdp_ask.runner.cdp_registry.bind_session_address"),
        patch("cdp_ask.runner._wake_debt_extras", return_value={}),
    ):
        await run_execution(req, execution_id="exec-cw", abort_check=AsyncMock(return_value=False))

    assert overlay.await_args.kwargs["allow_family_substitution"] is True


_SUBSTITUTION_SENTINEL = object()


@pytest.mark.asyncio
async def test_run_execution_code_surface_aborted_carries_ignore_flag() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        project_uuid="p",
        claude_surface="code",
        model="opus-5",
    )
    reg = MagicMock(registration_id="reg-ab", cdp_url="http://127.0.0.1:9226", target_id=None)
    converse = AsyncMock(return_value=[_converse_ok_result()])

    async def _overlay(*, run_once, **_kw):
        return await run_once(req.model), {}

    with (
        _run_execution_patches(reg, converse=converse, overlay=AsyncMock(side_effect=_overlay)),
        patch("cdp_ask.runner.abort_cleanup"),
    ):
        result = await run_execution(
            req,
            execution_id="exec-ab-code",
            abort_check=AsyncMock(return_value=True),
        )

    assert result["status"] == "aborted"
    assert result["project_uuid_ignored_for_code_surface"] is True


@pytest.mark.asyncio
async def test_run_execution_non_converse_overlay_disables_family_substitution_for_code() -> None:
    """converse=False + code uses converse overlay path (AC1); substitution still disabled."""
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=False,
        project_uuid="proj-only",
        claude_surface="code",
        model="opus-5",
        delete_after=False,
    )
    reg = MagicMock(registration_id="reg-ov", cdp_url="http://127.0.0.1:9226", target_id=None)
    converse = AsyncMock(return_value=[_converse_ok_result()])
    overlay = AsyncMock()

    async def _overlay(*, run_once, allow_family_substitution=_SUBSTITUTION_SENTINEL, **_kw):
        overlay.last_allow_family_substitution = allow_family_substitution
        return await run_once(req.model), {}

    overlay.side_effect = _overlay

    with _run_execution_patches(reg, converse=converse, overlay=overlay):
        await run_execution(req, execution_id="exec-ov", abort_check=AsyncMock(return_value=False))

    assert overlay.last_allow_family_substitution is False


@pytest.mark.asyncio
async def test_run_execution_code_no_project_uuid_non_converse_uses_converse_route() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=False,
        claude_surface="code",
        model="opus-5",
        delete_after=False,
    )
    reg = MagicMock(registration_id="reg-npu", cdp_url="http://127.0.0.1:9226", target_id=None)
    converse = AsyncMock(return_value=[_converse_ok_result()])
    run_ask = AsyncMock()

    async def _overlay(*, run_once, **_kw):
        return await run_once(req.model), {}

    with (
        _run_execution_patches(reg, converse=converse, overlay=AsyncMock(side_effect=_overlay)),
        patch("cdp_ask.runner.run_project_ask", new=run_ask),
    ):
        result = await run_execution(
            req,
            execution_id="exec-npu",
            abort_check=AsyncMock(return_value=False),
        )

    run_ask.assert_not_awaited()
    converse.assert_awaited_once()
    assert "project_uuid_ignored_for_code_surface" not in result


@pytest.mark.asyncio
async def test_run_execution_code_whitespace_project_uuid_non_converse_no_flag() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=False,
        project_uuid="  ",
        claude_surface="code",
        model="opus-5",
        delete_after=False,
    )
    reg = MagicMock(registration_id="reg-ws", cdp_url="http://127.0.0.1:9226", target_id=None)
    converse = AsyncMock(return_value=[_converse_ok_result()])

    async def _overlay(*, run_once, **_kw):
        return await run_once(req.model), {}

    with _run_execution_patches(reg, converse=converse, overlay=AsyncMock(side_effect=_overlay)):
        result = await run_execution(
            req,
            execution_id="exec-ws",
            abort_check=AsyncMock(return_value=False),
        )

    converse.assert_awaited_once()
    assert "project_uuid_ignored_for_code_surface" not in result


@pytest.mark.asyncio
async def test_run_execution_cowork_non_converse_no_project_raises() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=False,
        claude_surface="cowork",
        model="opus-5",
        delete_after=False,
        no_project_uuid=False,
    )
    reg = MagicMock(registration_id="reg-cv", cdp_url="http://127.0.0.1:9226", target_id=None)
    converse = AsyncMock()

    async def _overlay(*, run_once, **_kw):
        return await run_once(req.model), {}

    with (
        _run_execution_patches(reg, converse=converse, overlay=AsyncMock(side_effect=_overlay)),
        pytest.raises(ValueError, match="project_uuid required unless no_project_uuid=true"),
    ):
        await run_execution(req, execution_id="exec-cv", abort_check=AsyncMock(return_value=False))
