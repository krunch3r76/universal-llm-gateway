"""Admission tests for CDP model-endpoint front."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cdp_generate import (
    is_cdp_model,
    reject_cursor_sdk_seat_with_cdp,
    reject_dispatch_lane_with_cdp,
    reject_role_with_substrate_model,
)
from systems.frontier_consult.cdp_generate_worker import (
    ONBEHALF_POST_FAILED_STALL,
    _unread_latest_from_409,
    deliver_cdp_result_turn,
    format_cdp_result_body,
    format_onbehalf_delivery_failed_body,
)


def test_is_cdp_model() -> None:
    assert is_cdp_model("cdp/opus-4.8") is True
    assert is_cdp_model("cursor/grok-4.5") is False
    assert is_cdp_model("anthropic/claude-opus-4-8") is False
    assert is_cdp_model(None) is False


def test_reject_cursor_sdk_seat_with_cdp() -> None:
    reject_cursor_sdk_seat_with_cdp(
        seat=None, model="cdp/opus-4.8", request_id="r1"
    )
    reject_cursor_sdk_seat_with_cdp(
        seat="cursor-sdk", model="cursor/grok-4.5", request_id="r1"
    )
    with pytest.raises(FrontierEndpointError) as exc:
        reject_cursor_sdk_seat_with_cdp(
            seat="cursor-sdk", model="cdp/opus-4.8", request_id="r1"
        )
    assert exc.value.code == "cdp_cursor_sdk_seat_rejected"


def test_reject_role_with_substrate_model() -> None:
    reject_role_with_substrate_model(
        role=None, model="cdp/opus-5", request_id="r1"
    )
    reject_role_with_substrate_model(
        role="gatherer", model="openai/gpt-5.5", request_id="r1"
    )
    with pytest.raises(FrontierEndpointError) as exc:
        reject_role_with_substrate_model(
            role="gatherer", model="cdp/opus-5", request_id="r1"
        )
    assert exc.value.code == "substrate_model_role_conflict"
    with pytest.raises(FrontierEndpointError) as exc2:
        reject_role_with_substrate_model(
            role="reviewer", model="cursor/claude-opus-5", request_id="r1"
        )
    assert exc2.value.code == "substrate_model_role_conflict"


def test_reject_dispatch_lane_with_cdp() -> None:
    reject_dispatch_lane_with_cdp(
        dispatch_lane=None, model="cdp/opus-5", request_id="r1"
    )
    reject_dispatch_lane_with_cdp(
        dispatch_lane="", model="cdp/fable", request_id="r1"
    )
    reject_dispatch_lane_with_cdp(
        dispatch_lane="cursor-implement",
        model="cursor/grok-4.5",
        request_id="r1",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        reject_dispatch_lane_with_cdp(
            dispatch_lane="path-sim-admit-gate",
            model="cdp/opus-5",
            request_id="r1",
        )
    assert exc.value.code == "cdp_dispatch_lane_rejected"
    assert exc.value.field == "dispatch_lane"


def test_cdp_generate_module_scoped_from_lb_dispatch_lane_inference() -> None:
    """CDP admit must not call cursor-sdk path-sim lane normalization."""
    from pathlib import Path

    import systems.frontier_consult.cdp_generate as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "normalize_dispatch_lane" not in source
    assert "stamp_lb_review_spawn_fields" not in source


def test_contract_from_dispatch_lane_path_sim_admit_gate() -> None:
    from systems.frontier_consult.contract_derivation import contract_from_dispatch_lane

    assert contract_from_dispatch_lane("path-sim-admit-gate") == "consult"
    assert contract_from_dispatch_lane("cursor-sdk-implement") == "implement"


def test_stage_inputs_prepends_claude_slash_skills(tmp_path, monkeypatch) -> None:
    from claude_bundles import cdp_model_endpoint_staging as staging

    from systems.frontier_consult.cdp_generate import _stage_inputs

    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path)
    staged = _stage_inputs(
        execution_id="exec-skills-1",
        prompt="## ask\nhello\n",
        sidecar_ref=None,
        packet_path=None,
        skills=["reasoning-posture", "consult-posture"],
    )
    prompt_path = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills-1/prompt.md"
    )
    text = prompt_path.read_text(encoding="utf-8")
    assert text.startswith(
        "/frontier-reasoning-discipline\n/reasoning-posture\n/consult-posture\n"
    )
    assert "## ask" in text
    assert staged.staged is True


def test_stage_inputs_omitted_skills_gets_judgment_pair(tmp_path, monkeypatch) -> None:
    from claude_bundles import cdp_model_endpoint_staging as staging

    from systems.frontier_consult.cdp_generate import _stage_inputs

    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path)
    staged = _stage_inputs(
        execution_id="exec-skills-light",
        prompt="## light\n",
        sidecar_ref=None,
        packet_path=None,
        skills=None,
    )
    prompt_path = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills-light/prompt.md"
    )
    text = prompt_path.read_text(encoding="utf-8")
    assert text.startswith(
        "/reasoning-posture\n/frontier-reasoning-discipline\n"
    )
    assert "## light" in text
    assert staged.staged is True


def test_stage_inputs_inlines_non_claude_skills(tmp_path, monkeypatch) -> None:
    from claude_bundles import cdp_model_endpoint_staging as staging

    from systems.frontier_consult.cdp_generate import _stage_inputs

    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path)
    _stage_inputs(
        execution_id="exec-skills-2",
        prompt="BODY\n",
        sidecar_ref=None,
        packet_path=None,
        skills=["investigation-economy"],
    )
    prompt_path = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills-2/prompt.md"
    )
    text = prompt_path.read_text(encoding="utf-8")
    # Judgment pair always prepended as slash; caller non-Claude stays inline.
    assert text.startswith(
        "/reasoning-posture\n/frontier-reasoning-discipline\n"
    )
    assert "<skills_inline>" in text
    assert '<skill slug="investigation-economy"' in text
    assert "BODY" in text


def test_stage_inputs_inlines_code_mcp_skills_with_claude_slash(
    tmp_path, monkeypatch
) -> None:
    from claude_bundles import cdp_model_endpoint_staging as staging

    from systems.frontier_consult.cdp_generate import _stage_inputs

    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path)
    staged = _stage_inputs(
        execution_id="exec-skills-mixed",
        prompt="BODY\n",
        sidecar_ref=None,
        packet_path=None,
        skills=["path-sim", "reasoning-posture"],
    )
    prompt_path = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills-mixed/prompt.md"
    )
    text = prompt_path.read_text(encoding="utf-8")
    assert text.startswith(
        "/frontier-reasoning-discipline\n/reasoning-posture\n"
    )
    assert "/path-sim" not in text.split("<skills_inline>", 1)[0]
    assert '<skill slug="path-sim"' in text
    assert "truncated CDP inline excerpt for path-sim" in text
    # Sealed excerpt, not full SKILL.md (~18k).
    inline_block = text.split("<skills_inline>", 1)[1].split("</skills_inline>", 1)[0]
    assert len(inline_block) < 12000
    assert "BODY" in text
    assert staged.staged is True


def test_assert_model_carded_skips_cdp() -> None:
    from systems.frontier_consult.admission import assert_model_carded

    assert_model_carded("cdp/opus-4.8", request_id="r1", event_publisher=None)


def test_check_agent_model_consistency_skips_cdp_prefix() -> None:
    from agent_seat.registry import check_agent_model_consistency

    assert (
        check_agent_model_consistency("claude-web", "cdp/opus-4.8") is None
    )


def _ok_result() -> CdpGenerateResult:
    return CdpGenerateResult(
        ok=True,
        body="harvest body",
        execution_id="abcdef0123456789",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://notes/system/threads/r-prompt.md",
        picker_model="opus-4.8",
        archive_uri="cortex://notes/system/threads/archive.md",
    )


@pytest.mark.asyncio
async def test_deliver_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_post(**kwargs: object) -> bool:
        calls.append(str(kwargs.get("subject")))
        return len(calls) >= 2

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.post_cdp_turn",
        fake_post,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.asyncio.sleep",
        AsyncMock(),
    )
    ok = await deliver_cdp_result_turn(
        result=_ok_result(),
        thread_id="5583",
        to_agent="dispatch",
        request_id="r-retry",
    )
    assert ok is True
    assert len(calls) == 2
    assert all("DELIVERY FAILED" not in s for s in calls)


@pytest.mark.asyncio
async def test_deliver_terminal_delivery_failed_when_posts_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subjects: list[str] = []
    bodies: list[str] = []

    async def fake_post(**kwargs: object) -> bool:
        subjects.append(str(kwargs.get("subject")))
        bodies.append(str(kwargs.get("body")))
        return False

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.post_cdp_turn",
        fake_post,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.asyncio.sleep",
        AsyncMock(),
    )
    crit = MagicMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.logger.critical",
        crit,
    )
    ok = await deliver_cdp_result_turn(
        result=_ok_result(),
        thread_id="5583",
        to_agent="dispatch",
        request_id="r-term",
    )
    assert ok is False
    assert len(subjects) == 3
    assert subjects[0].startswith("cdp reply —")
    assert subjects[1].startswith("cdp reply —")
    assert subjects[2].startswith("cdp DELIVERY FAILED —")
    assert ONBEHALF_POST_FAILED_STALL in bodies[2]
    # R-admit-shaped wait: a from=cdp DELIVERY FAILED turn was attempted so
    # agent_bus.wait(from_agent=cdp) would terminalize when the bus is up.
    assert "DELIVERY FAILED" in subjects[2]
    crit.assert_called_once()


def test_format_onbehalf_delivery_failed_includes_stall_stage() -> None:
    text = format_onbehalf_delivery_failed_body(_ok_result())
    assert ONBEHALF_POST_FAILED_STALL in text
    assert "prior_archive_uri" in text


def test_format_cdp_result_body_upstream_overloaded() -> None:
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="abcdef0123456789",
        satellite_execution_id=None,
        prompt_uri="cortex://notes/system/threads/r-prompt.md",
        picker_model="opus-4.8",
        stall_stage="upstream_overloaded",
        error="project-ask HTTP 529",
        extras={"reason": "upstream_overloaded", "status_code": 529},
    )
    text = format_cdp_result_body(result)
    assert "status:failed reason=upstream_overloaded" in text
    assert "cdp FAILED" not in text


def test_format_onbehalf_delivery_failed_preserves_upstream_reason() -> None:
    overloaded = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="abcdef0123456789",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://notes/system/threads/r-prompt.md",
        picker_model="opus-4.8",
        stall_stage="upstream_overloaded",
        error="upstream overload-only harvest body",
        extras={"reason": "upstream_overloaded", "status_code": 529},
        archive_uri="cortex://notes/system/threads/archive.md",
    )
    text = format_onbehalf_delivery_failed_body(overloaded)
    assert "status:failed reason=upstream_overloaded" in text
    assert "prior_stall_stage: `upstream_overloaded`" in text
    assert "prior_reason: `upstream_overloaded`" in text


@pytest.mark.asyncio
async def test_emit_upstream_overload_friction_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult import cdp_generate_worker as worker

    posts: list[dict[str, Any]] = []

    class _Client:
        async def post(self, path: str, json: dict) -> Any:
            del path
            posts.append(json)
            return type("Resp", (), {"status_code": 200, "text": "ok"})()

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(worker, "make_async_client", lambda *a, **k: _Client())
    worker._UPSTREAM_OVERLOAD_FRICTION_EMITTED.clear()
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="exec-friction-dedup",
        satellite_execution_id=None,
        prompt_uri="cortex://notes/system/threads/r-prompt.md",
        picker_model="opus-4.8",
        stall_stage="upstream_overloaded",
        error="project-ask HTTP 529",
        extras={"reason": "upstream_overloaded", "status_code": 529},
    )
    await worker._emit_upstream_overload_friction(
        execution_id="exec-friction-dedup",
        thread_id="6386",
        result=result,
    )
    await worker._emit_upstream_overload_friction(
        execution_id="exec-friction-dedup",
        thread_id="6386",
        result=result,
    )
    assert len(posts) == 1
    note = posts[0]["arguments"]
    assert "execution_id=exec-friction-dedup" in note
    assert "service:universal-stargate" in note


def test_unread_latest_from_409_extracts_latest() -> None:
    resp = MagicMock()
    resp.status_code = 409
    resp.json.return_value = {
        "detail": {
            "error": "unread_turns_exist",
            "latest_turn_number": 16,
            "unread_turns": [{"turn_number": 16}],
        }
    }
    assert _unread_latest_from_409(resp) == 16


def test_unread_latest_from_409_ignores_other_errors() -> None:
    resp = MagicMock()
    resp.status_code = 409
    resp.json.return_value = {"detail": {"error": "other", "latest_turn_number": 9}}
    assert _unread_latest_from_409(resp) is None
    resp.status_code = 500
    assert _unread_latest_from_409(resp) is None


@pytest.mark.asyncio
async def test_post_cdp_turn_retries_after_unread_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent second CDP admit leaves to=cdp unread past pointer — remake+retry."""
    from systems.frontier_consult import cdp_generate_worker as worker

    class _Resp:
        def __init__(self, status_code: int, payload: dict | None = None) -> None:
            self.status_code = status_code
            self._payload = payload or {}
            self.text = str(payload or "")

        def json(self) -> dict:
            return self._payload

    posts: list[int] = []
    marks: list[int] = []

    class _Client:
        async def patch(self, path: str, json: dict, headers: dict) -> _Resp:
            del path, headers
            marks.append(int(json["through_turn"]))
            return _Resp(200)

        async def post(self, path: str, json: dict, headers: dict) -> _Resp:
            del path, json, headers
            posts.append(len(posts))
            if len(posts) == 1:
                return _Resp(
                    409,
                    {
                        "detail": {
                            "error": "unread_turns_exist",
                            "latest_turn_number": 16,
                            "unread_turns": [{"turn_number": 16}],
                        }
                    },
                )
            return _Resp(200)

    class _CM:
        async def __aenter__(self) -> _Client:
            return _Client()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        worker,
        "make_async_client",
        lambda *a, **k: _CM(),
    )
    ok = await worker.post_cdp_turn(
        thread_id="5737",
        to_agent="cursor",
        subject="cdp reply — abcd",
        body="body",
        request_id="r1",
        pointer_turn=15,
    )
    assert ok is True
    assert marks == [15, 16]
    assert len(posts) == 2


def test_team_dispatch_generate_body_accepts_purpose() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        contract="light-bounded",
        dispatch_thread_id="6451",
        model="cdp/opus-5",
        prompt="heal",
        purpose="operator-proxy",
    )
    assert body.purpose == "operator-proxy"


def test_team_dispatch_generate_body_purpose_optional() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        contract="light-bounded",
        dispatch_thread_id="6451",
        model="cdp/opus-5",
        prompt="consult",
    )
    assert body.purpose is None


@pytest.mark.asyncio
async def test_build_dispatch_body_cdp_raises_substrate_unimplemented() -> None:
    """Pipeline admission refuses cdp/ with structured substrate error (not 404)."""
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.service import (
        FrontierGenerateRequest,
        build_dispatch_body,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "R-admit dogfood"}],
        model="cdp/opus-4.8",
        role=None,
        dispatch_thread_id="dogfood-cdp-thread",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await build_dispatch_body(req)
    assert exc.value.code == "substrate_capability_unimplemented"
    assert exc.value.status_code == 501
    assert exc.value.details is not None
    assert exc.value.details["substrate"] == "cdp"
    assert exc.value.details["capability"] == "pipeline_dispatch_admission"
