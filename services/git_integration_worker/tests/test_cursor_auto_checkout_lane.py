"""cursor-auto nested checkout lane resolver tests."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.checkout_lane import (
    may_nest_under,
    resolve_nested_checkout_lane,
)
from services.git_integration_worker.cursor_auto.directive import build_sdk_message
from services.git_integration_worker.cursor_auto.nest_parent import resolve_nest_under
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.reporting_contract import (
    reporting_contract_block,
)
from services.git_integration_worker.cursor_sdk_lane_select import select_lane
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source_repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


def _job(*, contract: str = "implement", lane: str | None = None) -> AutoJob:
    return AutoJob(
        job_id="j-lane",
        thread_id="9554",
        turn_number=1,
        subject="DIRECTIVE",
        body="TYPE: DIRECTIVE\nscope: foo\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract=contract,
        lane=lane,
    )


@pytest.mark.parametrize(
    ("contract", "expected_lane", "expected_reason"),
    [
        ("implement", "B", "auto_implement_class"),
        ("verify", "B", "auto_implement_class"),
        ("confer", "A", "auto_named_a_non_implement"),
        ("investigate", "A", "auto_named_a_non_implement"),
        ("answer", "A", "auto_named_a_non_implement"),
        ("seed", "A", "auto_named_a_non_implement"),
        ("recon", "A", "auto_named_a_non_implement"),
        ("execute", "A", "auto_named_a_non_implement"),
        ("propagate", "A", "auto_named_a_non_implement"),
    ],
)
def test_resolve_nested_checkout_lane_contracts(
    contract: str,
    expected_lane: str,
    expected_reason: str,
) -> None:
    lane, reason = resolve_nested_checkout_lane(_job(contract=contract), read_only=False)
    assert lane == expected_lane
    assert reason == expected_reason


def test_explicit_lane_a_on_implement() -> None:
    lane, reason = resolve_nested_checkout_lane(
        _job(contract="implement", lane="A"),
        read_only=False,
    )
    assert lane == "A"
    assert reason == "explicit"


def test_read_only_does_not_select_b() -> None:
    lane, reason = resolve_nested_checkout_lane(_job(contract="implement"), read_only=True)
    assert lane == "A"
    assert reason == "read_only"


def test_auto_light_bounded_omit_stays_a(git_repo) -> None:
    req = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="d",
        execution_id="e",
        message="x",
        admitted_via="cursor-auto",
    )
    lane, _, reason = select_lane(
        req=req,
        regime_active=True,
        source_repo=git_repo,
        files_expected=[],
        contract="light-bounded",
    )
    assert lane == "A"
    assert reason == "opt_out"
    auto_lane, auto_reason = resolve_nested_checkout_lane(
        _job(contract="confer"),
        read_only=False,
    )
    assert auto_lane == "A"
    assert auto_reason == "auto_named_a_non_implement"


def test_stargate_empty_scope_still_opt_out_a(git_repo) -> None:
    req = CursorDispatchRequest(
        thread_id="t",
        model="cursor/composer-2.5",
        dispatch_id="d",
        execution_id="e",
        message="x",
        admitted_via="stargate",
    )
    lane, _, reason = select_lane(
        req=req,
        regime_active=True,
        source_repo=git_repo,
        files_expected=[],
        contract="pure-mechanical",
    )
    assert lane == "A"
    assert reason == "opt_out"


def test_may_nest_under_refuses_foreign_lane_a() -> None:
    job = _job()
    assert may_nest_under(holder_lane="A", holder_thread_id="9554", job=job) is False
    assert may_nest_under(holder_lane="B", holder_thread_id="other", job=job) is False
    assert may_nest_under(holder_lane="B", holder_thread_id="9554", job=job) is True


@pytest.mark.asyncio
async def test_resolve_nest_under_refuses_lane_a_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job(contract="implement")
    gate_plan = {"action": "nest_park", "reason": "gate_at_capacity_prefer_park", "gate": {}}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nest_parent.CursorDispatchLedger.instance",
        lambda: MagicMock(
            lease_snapshot=MagicMock(
                return_value={"holder_dispatch_id": "auto-7102f9d500d6"}
            ),
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nest_parent._holder_context",
        lambda _holder_id: ("A", "9554"),
    )

    result = await resolve_nest_under(
        job,
        client=AsyncMock(),
        queue=MagicMock(),
        gate_plan=gate_plan,
        work_bounded=False,
        contract="implement",
    )
    assert result is None
    assert gate_plan["action"] == "dispatch_now"


def test_reflex_read_only_does_not_stamp_b(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    captured: dict[str, object] = {}

    class _FakeCM:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> object:
            client = AsyncMock()

            async def _post(url: str, json: dict[str, object]) -> MagicMock:
                captured.update(json)
                resp = MagicMock()
                resp.status_code = 200
                resp.content = b'{"admitted": true}'
                resp.json.return_value = {"admitted": True}
                return resp

            client.post = _post
            return client

        async def __aexit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient",
        _FakeCM,
    )
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        submit_nested_dispatch,
    )

    job = _job(contract="implement")
    asyncio.run(
        submit_nested_dispatch(
            job,
            model_id="cursor/gpt-5.6-luna",
            handoff_contract="light-bounded",
            message="read",
            read_only=True,
            bind_job=False,
        )
    )
    assert "lane" not in captured


def test_build_sdk_message_lane_b_omits_lane_a_checkpoint() -> None:
    message = build_sdk_message("TYPE: DIRECTIVE\nscope: foo\n", contract="implement", lane="B")
    assert "Lane-A checkpoint" not in message
    assert "LAND DISPOSITION" in reporting_contract_block(lane="B")


def test_build_sdk_message_lane_a_keeps_checkpoint() -> None:
    message = build_sdk_message("TYPE: DIRECTIVE\nscope: foo\n", contract="implement", lane="A")
    assert "Lane-A checkpoint" in message
    assert "status_claim:" in message


def test_implement_nested_post_stamps_lane_b(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeCM:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> object:
            client = AsyncMock()

            async def _post(url: str, json: dict[str, object]) -> MagicMock:
                captured.update(json)
                resp = MagicMock()
                resp.status_code = 200
                resp.content = b'{"admitted": true}'
                resp.json.return_value = {"admitted": True}
                return resp

            client.post = _post
            return client

        async def __aexit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient",
        _FakeCM,
    )
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        submit_nested_dispatch,
    )

    asyncio.run(
        submit_nested_dispatch(
            _job(contract="implement"),
            model_id="cursor/composer-2.5",
            handoff_contract="pure-mechanical",
            message="go",
        )
    )
    assert captured.get("lane") == "B"
    assert captured.get("admitted_via") == "cursor-auto"
