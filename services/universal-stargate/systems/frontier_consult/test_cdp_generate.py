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
    cdp_result_subject,
    deliver_cdp_result_turn,
    format_cdp_result_body,
    format_onbehalf_delivery_failed_body,
)


def test_is_cdp_model() -> None:
    assert is_cdp_model("cdp/opus-4.8") is True
    assert is_cdp_model("cursor/grok-4.6") is False
    assert is_cdp_model("anthropic/claude-opus-4-8") is False
    assert is_cdp_model(None) is False


def test_reject_cursor_sdk_seat_with_cdp() -> None:
    reject_cursor_sdk_seat_with_cdp(
        seat=None, model="cdp/opus-4.8", request_id="r1"
    )
    reject_cursor_sdk_seat_with_cdp(
        seat="cursor-sdk", model="cursor/grok-4.6", request_id="r1"
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
        model="cursor/grok-4.6",
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
        "/ulg-for-llms\n/reasoning-posture\n/consult-posture\n"
    )
    assert "## ask" in text
    assert staged.staged is True


def test_stage_inputs_omitted_skills_gets_judgment_skill(tmp_path, monkeypatch) -> None:
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
        "/ulg-for-llms\n/reasoning-posture\n"
    )
    assert "## light" in text
    assert staged.staged is True
    assert "<skills_inline>" not in text
    assert 'fs(sandbox="workspaces"' not in text


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
    # Judgment skill always prepended as slash; caller non-Claude stays inline.
    assert text.startswith(
        "/ulg-for-llms\n/reasoning-posture\n"
    )
    assert "<skills_inline>" in text
    assert '<skill slug="investigation-economy"' in text
    assert "BODY" in text
    assert "not on this seat's Skill loader" in text
    assert 'fs(sandbox="workspaces", op="read", path="' in text
    assert "investigation-economy/SKILL.md" in text


def test_stage_inputs_rejects_path_sim_skills(tmp_path, monkeypatch) -> None:
    """a:27430 — CDP staging rejects path-sim; maps to skills field 422 upstream."""
    from claude_bundles import cdp_model_endpoint_staging as staging
    from claude_bundles.cdp_model_endpoint_staging import CdpStagingError

    from systems.frontier_consult.cdp_generate import _stage_inputs

    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path)
    with pytest.raises(CdpStagingError) as excinfo:
        _stage_inputs(
            execution_id="exec-skills-path-sim-reject",
            prompt="BODY\n",
            sidecar_ref=None,
            packet_path=None,
            skills=["path-sim", "reasoning-posture"],
        )
    assert excinfo.value.code == "cdp_skills_path_sim_rejected"


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
        skills=["investigation-economy", "reasoning-posture"],
    )
    prompt_path = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills-mixed/prompt.md"
    )
    text = prompt_path.read_text(encoding="utf-8")
    assert text.startswith(
        "/ulg-for-llms\n/reasoning-posture\n"
    )
    assert "/investigation-economy" not in text.split("<skills_inline>", 1)[0]
    assert '<skill slug="investigation-economy"' in text
    assert "BODY" in text
    assert staged.staged is True
    assert "not on this seat's Skill loader" in text
    assert 'fs(sandbox="workspaces", op="read", path="' in text


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


def _ok_result_no_archive(*, body: str = "harvest body") -> CdpGenerateResult:
    return CdpGenerateResult(
        ok=True,
        body=body,
        execution_id="abcdef0123456789",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://notes/system/threads/r-prompt.md",
        picker_model="fable-5",
        archive_uri=None,
    )


@pytest.mark.asyncio
async def test_deliver_oversized_with_archive_posts_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1 — prefer existing archive_uri; no sidecar write."""
    from systems.frontier_consult import cdp_onbehalf_delivery as delivery

    sidecar_calls: list[dict[str, object]] = []
    posted_bodies: list[str] = []

    async def fake_sidecar(**kwargs: object) -> None:
        sidecar_calls.append(dict(kwargs))

    async def fake_post(**kwargs: object) -> bool:
        posted_bodies.append(str(kwargs.get("body")))
        return True

    huge = "x" * 170_000
    result = CdpGenerateResult(
        ok=True,
        body=huge,
        execution_id="b9357ba901234567",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://notes/system/threads/r-prompt.md",
        picker_model="fable-5",
        archive_uri="cortex://notes/system/threads/cdp-ask-archive-existing.md",
        content_proof_sha256="abc123sha256",
    )

    monkeypatch.setattr(delivery, "write_cdp_harvest_sidecar", fake_sidecar)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.post_cdp_turn",
        fake_post,
    )

    ok = await deliver_cdp_result_turn(
        result=result,
        thread_id="9880",
        to_agent="dispatch",
        request_id="r-oversized-archive",
    )
    assert ok is True
    assert sidecar_calls == []
    assert len(posted_bodies) == 1
    body = posted_bodies[0]
    assert len(body) <= delivery.BUS_MAX_BODY_CHARS
    assert "cortex://notes/system/threads/cdp-ask-archive-existing.md" in body
    assert "abc123sha256" in body
    assert huge not in body
    assert "relocated to cortex" in body


@pytest.mark.asyncio
async def test_deliver_oversized_without_archive_writes_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2 — thread_sidecar_write then pointer citing returned uri+sha256."""
    from systems.frontier_consult import cdp_onbehalf_delivery as delivery
    from systems.frontier_consult.cdp_onbehalf_delivery import SidecarResult

    sidecar_calls: list[dict[str, object]] = []
    posted_bodies: list[str] = []

    async def fake_sidecar(**kwargs: object) -> SidecarResult:
        sidecar_calls.append(dict(kwargs))
        return SidecarResult(
            uri="cortex://notes/system/threads/9880-sidecar.md",
            sha256="deadbeefsha256",
            body_chars=len(str(kwargs.get("content"))),
        )

    async def fake_post(**kwargs: object) -> bool:
        posted_bodies.append(str(kwargs.get("body")))
        return True

    huge = "y" * 170_000
    result = _ok_result_no_archive(body=huge)

    monkeypatch.setattr(delivery, "write_cdp_harvest_sidecar", fake_sidecar)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.post_cdp_turn",
        fake_post,
    )

    ok = await deliver_cdp_result_turn(
        result=result,
        thread_id="9880",
        to_agent="dispatch",
        request_id="r-oversized-sidecar",
    )
    assert ok is True
    assert len(sidecar_calls) == 1
    assert sidecar_calls[0]["oversized"] is True
    assert sidecar_calls[0]["content"] == huge
    assert len(posted_bodies) == 1
    body = posted_bodies[0]
    assert len(body) <= delivery.BUS_MAX_BODY_CHARS
    assert "cortex://notes/system/threads/9880-sidecar.md" in body
    assert "deadbeefsha256" in body
    assert huge not in body


@pytest.mark.asyncio
async def test_deliver_under_limit_inline_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3 — metadata headers then harvest inline, byte-for-byte."""
    posted_bodies: list[str] = []

    async def fake_post(**kwargs: object) -> bool:
        posted_bodies.append(str(kwargs.get("body")))
        return True

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.post_cdp_turn",
        fake_post,
    )
    result = _ok_result_no_archive(body="harvest body")
    ok = await deliver_cdp_result_turn(
        result=result,
        thread_id="5583",
        to_agent="dispatch",
        request_id="r-inline",
    )
    assert ok is True
    assert len(posted_bodies) == 1
    metadata = format_cdp_result_body(result)
    assert posted_bodies[0] == f"{metadata}\n\nharvest body"


@pytest.mark.asyncio
async def test_deliver_oversized_sidecar_fail_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4 — sidecar write failed: no success post; DELIVERY FAILED terminal."""
    from systems.frontier_consult import cdp_onbehalf_delivery as delivery

    sidecar_calls: list[dict[str, object]] = []
    subjects: list[str] = []

    async def fake_sidecar(**kwargs: object) -> None:
        sidecar_calls.append(dict(kwargs))
        return None

    async def fake_post(**kwargs: object) -> bool:
        subjects.append(str(kwargs.get("subject")))
        return False

    huge = "z" * 170_000
    result = _ok_result_no_archive(body=huge)

    monkeypatch.setattr(delivery, "write_cdp_harvest_sidecar", fake_sidecar)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.post_cdp_turn",
        fake_post,
    )
    monkeypatch.setattr(delivery.asyncio, "sleep", AsyncMock())

    ok = await deliver_cdp_result_turn(
        result=result,
        thread_id="9880",
        to_agent="dispatch",
        request_id="r-sidecar-fail",
    )
    assert ok is False
    assert len(sidecar_calls) == 1
    assert all("cdp reply" not in s for s in subjects)
    assert subjects[0].startswith("cdp DELIVERY FAILED —")
    assert ONBEHALF_POST_FAILED_STALL in format_onbehalf_delivery_failed_body(result)


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
        "systems.frontier_consult.cdp_onbehalf_delivery.asyncio.sleep",
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
        "systems.frontier_consult.cdp_onbehalf_delivery.asyncio.sleep",
        AsyncMock(),
    )
    crit = MagicMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_onbehalf_delivery.logger.critical",
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
    # R-admit-shaped wait: a from=web-anthropic DELIVERY FAILED turn was attempted so
    # agent_bus.wait(from_agent=web-anthropic) would terminalize when the bus is up.
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
        error="cdp-ask HTTP 529",
        extras={"reason": "upstream_overloaded", "status_code": 529},
    )
    text = format_cdp_result_body(result)
    assert "status:failed reason=upstream_overloaded" in text
    assert "cdp FAILED" not in text


def test_format_cdp_result_body_completed_without_proof_honest() -> None:
    result = CdpGenerateResult(
        ok=False,
        body="SKILLS_PROBE_OK",
        execution_id="abcdef0123456789",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://notes/system/threads/r-prompt.md",
        picker_model="fable-5",
        stall_stage="completed_without_proof",
        error="chat harvest lacks attested_model (archive_uri alone insufficient — AC-S1-b)",
        archive_uri="cortex://notes/system/threads/cdp-ask-archive-new.md",
        extras={
            "deliverable_present_unproven": True,
            "recovery": (
                "poll satellite / read archive_uri; verify body manually; "
                "do not blind re-dispatch"
            ),
        },
    )
    text = format_cdp_result_body(result)
    assert "- archive_uri: `cortex://notes/system/threads/cdp-ask-archive-new.md`" in text
    assert "- body_len: 15" in text
    assert "- deliverable_present_unproven: true" in text
    assert "do not blind re-dispatch" in text
    assert "without archive_uri" not in text


def test_cdp_result_subject_unverified_not_failed() -> None:
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="3f492a7c344a491b",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://p.md",
        picker_model="fable-5-high",
        stall_stage="observer_unverified",
        error="model select failed: picker",
        extras={"chat_url": "https://claude.ai/cowork/cse_abc"},
    )
    assert cdp_result_subject(result) == "cdp UNVERIFIED — 3f492a7c"
    text = format_cdp_result_body(result)
    assert "# CDP generate UNVERIFIED" in text
    assert "# CDP generate FAILED" not in text
    assert "chat_url:" in text


def test_cdp_result_subject_reconcile_abandoned_unverifiable() -> None:
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="abcdef0123456789",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        stall_stage="reconcile_abandoned_unverifiable",
        error="horizon unverifiable",
        extras={"chat_url": "https://claude.ai/cowork/cse_abc"},
    )
    assert cdp_result_subject(result).startswith("cdp UNVERIFIED")


def test_cdp_result_subject_weekly_limit_stays_failed() -> None:
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="abcdef0123456789",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        stall_stage="weekly_limit",
        error="product weekly-limit banner (not a seat reply)",
    )
    assert cdp_result_subject(result).startswith("cdp FAILED")


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
        error="cdp-ask HTTP 529",
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
    """Concurrent second CDP admit leaves endpoint unread past pointer — remake+retry."""
    from systems.frontier_consult import cdp_generate_worker as worker

    class _Resp:
        def __init__(self, status_code: int, payload: dict | None = None) -> None:
            self.status_code = status_code
            self._payload = payload or {}
            self.text = str(payload or "")

        def json(self) -> dict:
            return self._payload

    posts: list[int] = []
    marks: list[tuple[int, str]] = []

    class _Client:
        async def patch(self, path: str, json: dict, headers: dict) -> _Resp:
            del path, headers
            marks.append((int(json["through_turn"]), str(json["agent"])))
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
    # Dual mark: canonical web-anthropic + legacy cdp per through_turn
    assert marks == [
        (15, "web-anthropic"),
        (15, "cdp"),
        (16, "web-anthropic"),
        (16, "cdp"),
    ]
    assert len(posts) == 2
    # On-behalf posts as endpoint address
    assert worker.CDP_REPLY_FROM == "web-anthropic"

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


def test_default_operator_seat_binding_from_thread_id() -> None:
    from systems.frontier_consult.cdp_generate import default_operator_seat_binding

    lane, kind = default_operator_seat_binding(
        purpose="operator-proxy",
        parent_thread=None,
        mission_kind=None,
        thread_id="6655",
    )
    assert lane == "6655"
    assert kind == "root"


def test_default_operator_seat_binding_preserves_hop() -> None:
    from systems.frontier_consult.cdp_generate import default_operator_seat_binding

    lane, kind = default_operator_seat_binding(
        purpose="operator-proxy",
        parent_thread="6655",
        mission_kind="hop",
        thread_id="other",
    )
    assert lane == "6655"
    assert kind == "hop"


def test_default_operator_seat_binding_skips_ask() -> None:
    from systems.frontier_consult.cdp_generate import default_operator_seat_binding

    lane, kind = default_operator_seat_binding(
        purpose="ask",
        parent_thread=None,
        mission_kind=None,
        thread_id="6655",
    )
    assert lane == "6655"
    assert kind == "root"


def test_default_operator_seat_binding_review_from_thread_id() -> None:
    from systems.frontier_consult.cdp_generate import default_operator_seat_binding

    lane, kind = default_operator_seat_binding(
        purpose="review",
        parent_thread=None,
        mission_kind=None,
        thread_id="9638",
    )
    assert lane == "9638"
    assert kind == "root"


def test_refuse_second_external_gate_at_fire_when_lane_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda: {
            "rows": [
                {
                    "execution_id": "exec-live-gate",
                    "parent_thread": "9638",
                    "status": "running",
                    "purpose": "review",
                }
            ]
        },
    )
    with pytest.raises(FrontierEndpointError) as exc:
        refuse_second_external_gate_at_fire(
            purpose="review",
            parent_thread="9638",
            thread_id="9638",
            request_id="req-ac9",
        )
    assert exc.value.code == "cdp_external_gate_live"


def _capture_mission_provenance(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from systems.frontier_consult import cdp_mission_provenance as prov

    published: list[Any] = []
    monkeypatch.setattr(prov, "publish_frontier_event", published.append)
    return published


def test_mission_provenance_records_synthesized_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult.cdp_mission_provenance import observe_mission_binding

    published = _capture_mission_provenance(monkeypatch)
    observe_mission_binding(
        purpose="operator-proxy",
        dispatch_thread_id="6655",
        parent_thread="6655",
        mission_kind="root",
        synthesized=True,
    )

    assert len(published) == 1
    payload = published[0].payload
    assert payload["synthesized"] is True
    assert payload["parent_thread"] == "6655"
    assert payload["mission_kind"] == "root"
    assert published[0].signal == "frontier.cdp.mission.provenance"


def test_mission_provenance_silent_on_declared_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that named its own parent is the healthy path — no signal."""
    from systems.frontier_consult.cdp_mission_provenance import observe_mission_binding

    published = _capture_mission_provenance(monkeypatch)
    observe_mission_binding(
        purpose="mission",
        dispatch_thread_id="6655",
        parent_thread="7186",
        mission_kind="root",
        synthesized=False,
    )

    assert published == []


def test_mission_provenance_ignores_ask_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult.cdp_mission_provenance import observe_mission_binding

    published = _capture_mission_provenance(monkeypatch)
    observe_mission_binding(
        purpose="ask",
        dispatch_thread_id="6655",
        parent_thread=None,
        mission_kind=None,
        synthesized=True,
    )

    assert published == []


@pytest.mark.asyncio
async def test_dispatch_cdp_generate_forwards_generation_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """AC-S1-a: generation_options harvest knobs reach run_cdp_worker."""
    from unittest.mock import AsyncMock, MagicMock

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    monkeypatch.setattr(mod, "_stage_inputs", lambda **kw: MagicMock(
        prompt_uri="cortex://notes/system/ephemeral/prompt.md", staged=True
    ))
    monkeypatch.setattr(mod, "post_pointer_turn", AsyncMock(return_value=2))
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(mod, "build_handoff_result", lambda **kw: {
        "handoff_status": "ok",
        "poll_hint": {"thread_id": "1", "from_agent": "web-anthropic"},
    })
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)

    captured: list[dict[str, object]] = []
    pending: list[object] = []

    async def _fake_worker(**kwargs: object) -> None:
        captured.append(dict(kwargs))

    class _FakeTask:
        def add_done_callback(self, _cb: object) -> None:
            return None

        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            return None

    def _capture_task(coro: object, **kwargs: object) -> _FakeTask:
        pending.append(coro)
        return _FakeTask()

    monkeypatch.setattr(mod, "run_cdp_worker", _fake_worker)
    monkeypatch.setattr(mod.asyncio, "create_task", _capture_task)

    body = TeamDispatchGenerateBody(
        op="generate",
        contract="light-bounded",
        dispatch_thread_id="6451",
        model="cdp/fable",
        prompt="consult",
        generation_options={
            "harvest_source": "output-file",
            "expected_size": "large",
            "download_output": True,
        },
    )
    response = MagicMock()
    response.status_code = 202
    await mod.dispatch_cdp_generate(
        request_id="req-gen-opts",
        body=body,
        response=response,
    )
    assert pending
    await pending[0]
    assert captured
    kw = captured[0]
    assert kw["harvest_source"] == "output-file"
    assert kw["expected_size"] == "large"
    assert kw["download_output"] is True


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
