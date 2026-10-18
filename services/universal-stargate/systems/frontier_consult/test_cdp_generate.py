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
    PostOutcome,
    cdp_result_subject,
    deliver_cdp_result_turn,
    format_cdp_result_body,
    format_onbehalf_delivery_failed_body,
)


def test_is_cdp_model() -> None:
    assert is_cdp_model("cdp/opus-4.8") is True
    assert is_cdp_model("cursor/grok-4.7") is False
    assert is_cdp_model("anthropic/claude-opus-4-8") is False
    assert is_cdp_model(None) is False


def test_reject_cursor_sdk_seat_with_cdp() -> None:
    reject_cursor_sdk_seat_with_cdp(seat=None, model="cdp/opus-4.8", request_id="r1")
    reject_cursor_sdk_seat_with_cdp(
        seat="cursor-sdk", model="cursor/grok-4.7", request_id="r1"
    )
    with pytest.raises(FrontierEndpointError) as exc:
        reject_cursor_sdk_seat_with_cdp(
            seat="cursor-sdk", model="cdp/opus-4.8", request_id="r1"
        )
    assert exc.value.code == "cdp_cursor_sdk_seat_rejected"


def test_reject_role_with_substrate_model() -> None:
    reject_role_with_substrate_model(role=None, model="cdp/opus-5", request_id="r1")
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
    reject_dispatch_lane_with_cdp(dispatch_lane="", model="cdp/fable", request_id="r1")
    reject_dispatch_lane_with_cdp(
        dispatch_lane="cursor-implement",
        model="cursor/grok-4.7",
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

    assert contract_from_dispatch_lane("path-sim-admit-gate") == "confer"
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
    # Spec fork 8 / staging marker (stage_cdp_prompt_with_skills):
    # shared_sync floor is the cdp-required-skills marker, not a leading slash block.
    assert (
        "<!--cdp-required-skills:reasoning-posture,consult-posture-->" in text
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
    # Omitted skills: judgment floor only (spec acceptance item 3; reasoning-posture).
    assert "<!--cdp-required-skills:reasoning-posture-->" in text
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
    # Judgment skill is the marker; caller non-Claude stays inline.
    # stage_cdp_prompt_with_skills (spec acceptance item 3 judgment floor).
    # render_cdp_required_authority: floor plus the inlined non-Claude slug.
    assert (
        "<!--cdp-required-skills:reasoning-posture,investigation-economy-->" in text
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
    # stage_cdp_prompt_with_skills: marker, not a leading slash.
    # render_cdp_required_authority: caller slug then the judgment floor.
    assert (
        "<!--cdp-required-skills:investigation-economy,reasoning-posture-->" in text
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

    assert check_agent_model_consistency("claude-web", "cdp/opus-4.8") is None


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

    async def fake_post(**kwargs: object) -> PostOutcome:
        posted_bodies.append(str(kwargs.get("body")))
        return PostOutcome(ok=True, http_status=201)

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

    async def fake_post(**kwargs: object) -> PostOutcome:
        posted_bodies.append(str(kwargs.get("body")))
        return PostOutcome(ok=True, http_status=201)

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

    async def fake_post(**kwargs: object) -> PostOutcome:
        posted_bodies.append(str(kwargs.get("body")))
        return PostOutcome(ok=True, http_status=201)

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

    async def fake_post(**kwargs: object) -> PostOutcome:
        subjects.append(str(kwargs.get("subject")))
        return PostOutcome(ok=False, http_status=503, detail_preview="sidecar fail")

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

    async def fake_post(**kwargs: object) -> PostOutcome:
        calls.append(str(kwargs.get("subject")))
        ok = len(calls) >= 2
        return PostOutcome(ok=ok, http_status=201 if ok else 409)

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

    async def fake_post(**kwargs: object) -> PostOutcome:
        subjects.append(str(kwargs.get("subject")))
        bodies.append(str(kwargs.get("body")))
        return PostOutcome(
            ok=False,
            http_status=409,
            detail_preview='{"detail":{"error":"unread_turns_exist"}}',
        )

    published: list[dict[str, object]] = []

    def capture_publish(factory: Any, **kwargs: Any) -> bool:
        if factory.__name__ == "CdpGenerateDeliveryFailed":
            published.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.post_cdp_turn",
        fake_post,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_onbehalf_delivery.asyncio.sleep",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_events.publish_cdp_kwargs",
        capture_publish,
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
    assert "DELIVERY FAILED" in subjects[2]
    crit.assert_called_once()
    assert len(published) == 1
    assert published[0]["http_status"] == 409
    assert published[0]["detail_preview"] is not None
    assert "unread_turns_exist" in str(published[0]["detail_preview"])


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
        error=(
            "chat harvest lacks attested_model "
            "(archive_uri alone insufficient — AC-S1-b)"
        ),
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
    assert (
        "- archive_uri: `cortex://notes/system/threads/cdp-ask-archive-new.md`" in text
    )
    assert "- body_len: 15" in text
    assert "- deliverable_present_unproven: true" in text
    assert "do not blind re-dispatch" in text
    assert "without archive_uri" not in text


def test_format_cdp_result_body_select_no_attest() -> None:
    from claude_bundles.chat_model_match import select_no_attest_status

    record = select_no_attest_status(
        requested="fable-5.1",
        before="Model: Opus 5.5 Medium",
        after="Model: Opus 5.5 High",
        matched="Fable 5.1For your toughest challenges",
        path="discover",
        available=["Fable 5.1For your toughest challenges"],
        as_of="2026-09-22T00:00:00+00:00",
    )
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="ae28e25aa80a46bb",
        satellite_execution_id=None,
        prompt_uri="cortex://p.md",
        picker_model="fable-5.1",
        stall_stage="select_no_attest",
        error=f"model select failed: {record}",
        extras={"chat_url": "https://claude.ai/cowork/cse_abc"},
    )
    assert cdp_result_subject(result).startswith("cdp FAILED")
    text = format_cdp_result_body(result)
    assert "# CDP generate FAILED" in text
    assert "- stall_stage: `select_no_attest`" in text
    assert "- source: `cdp.model_select`" in text
    assert "- step: `select_no_attest`" in text
    assert "- before: `Model: Opus 5.5 Medium`" in text
    assert "- after: `Model: Opus 5.5 High`" in text
    assert "observer_unverified" not in text


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
    assert "cse:" in text
    assert "https://claude.ai/cowork/cse_abc" in text


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
        async def post(self, path: str, json: dict, headers: dict) -> Any:
            del path
            assert headers.get("X-ULG-Seat") == "stargate"
            assert headers.get("X-ULG-Caller") == "cdp_generate_worker"
            assert headers.get("X-ULG-Surface") == "code"
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


@pytest.mark.asyncio
async def test_post_cdp_turn_on_behalf_marks_pointer_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On-behalf posts bypass inbox gate; mark read uses exact pointer turn only."""
    from systems.frontier_consult import cdp_generate_worker as worker

    class _Resp:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.text = "ok"

    marks: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []

    class _Client:
        async def patch(self, path: str, json: dict, headers: dict) -> _Resp:
            del path, headers
            marks.append(dict(json))
            return _Resp(200)

        async def post(self, path: str, json: dict, headers: dict) -> _Resp:
            del path, headers
            posts.append(dict(json))
            return _Resp(201)

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
    outcome = await worker.post_cdp_turn(
        thread_id="5737",
        to_agent="cursor",
        subject="cdp reply — abcd",
        body="body",
        request_id="r1",
        pointer_turn=15,
    )
    assert outcome.ok is True
    assert outcome.http_status == 201
    assert marks == [{"turn_numbers": [15]}]
    assert len(posts) == 1
    assert posts[0]["on_behalf"] is True
    assert posts[0]["from"] == "web-anthropic"


@pytest.mark.asyncio
async def test_finalize_ok_harvest_failed_delivery_terminates_with_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult import cdp_generate_reconcile as reconcile
    from systems.frontier_consult.cdp_dispatch_envelope import record_cdp_admit

    terminate_calls: list[dict[str, object]] = []

    record_cdp_admit(
        execution_id="exec-fail-deliver",
        thread_id="5583",
        pointer_turn=2,
        admit_reason="ok",
        caller_supplied_thread=True,
    )

    async def _terminate(**kwargs: object) -> bool:
        terminate_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        "systems.frontier_consult.handoff.terminate_handoff_dispatch",
        _terminate,
    )
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", lambda *a, **k: True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=False),
    )
    reconcile.upsert_inflight_leg(
        execution_id="exec-fail-deliver",
        request_id="req-fail-deliver",
        thread_id="5583",
        pointer_turn=2,
        caller_agent="dispatch",
        prompt_uri="cortex://p.md",
        model_id="cdp/fable",
        max_wall_s=1800.0,
    )
    result = CdpGenerateResult(
        ok=True,
        body="harvest",
        execution_id="exec-fail-deliver",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://p.md",
        picker_model="fable-5",
        archive_uri="cortex://notes/system/threads/harvest.md",
    )
    await reconcile.finalize_cdp_generate(
        result=result,
        request_id="req-fail-deliver",
        thread_id="5583",
        to_agent="dispatch",
        pointer_turn=2,
        via="worker",
    )
    failed = [c for c in terminate_calls if c.get("terminal_status") == "failed"]
    assert len(failed) == 1
    assert failed[0]["archive_uri"] == "cortex://notes/system/threads/harvest.md"


def test_team_dispatch_generate_body_accepts_purpose() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        dispatch_thread_id="6451",
        model="cdp/opus-5",
        prompt="heal",
        purpose="operator-proxy",
    )
    assert body.purpose == "operator-proxy"


def test_team_dispatch_generate_body_accepts_parent_thread() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        dispatch_thread_id="6451",
        model="cdp/opus-5-high",
        prompt="navigator doorbell",
        parent_thread="10479",
    )
    assert body.parent_thread == "10479"


def test_team_dispatch_generate_body_purpose_optional() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
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
    assert lane is None
    assert kind is None


def test_default_operator_seat_binding_binds_review_from_thread_id() -> None:
    from systems.frontier_consult.cdp_generate import default_operator_seat_binding

    lane, kind = default_operator_seat_binding(
        purpose="review",
        parent_thread=None,
        mission_kind=None,
        thread_id="9638",
    )
    assert lane == "9638"
    assert kind == "root"


def test_refuse_second_external_gate_probe_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C4 — active-work probe fault on fire path → 503 cdp_gate_probe_failed."""
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    class _BrokenClient:
        def _request(self, method: str, path: str) -> dict[str, object]:
            raise ConnectionError("cdp_ask unreachable")

    monkeypatch.setattr(
        "cdp_ask.lane_snapshot.CdpAskClient",
        lambda: _BrokenClient(),
    )
    with pytest.raises(FrontierEndpointError) as exc:
        refuse_second_external_gate_at_fire(
            purpose="review",
            parent_thread="9638",
            thread_id="9638",
            request_id="req-c4-probe",
        )
    assert exc.value.code == "cdp_gate_probe_failed"
    assert exc.value.status_code == 503
    assert exc.value.field == "active-work"
    assert "active-work probe failed" in exc.value.reason


def test_refuse_second_external_gate_at_fire_when_lane_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [
                {
                    "execution_id": "exec-live-gate",
                    "parent_thread": "9638",
                    "status": "running",
                    "stream_state": "running",
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


def test_refuse_second_external_gate_seated_rows_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seated identity without a live stream must not block review external gate."""
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [],
            "seated_rows": [
                {
                    "execution_id": "exec-seated-only",
                    "parent_thread": "10128",
                    "seat_state": "active",
                    "stream_state": "none",
                    "purpose": "review",
                    "registration_id": "reg-seated",
                }
            ],
            "seat_rows": [],
        },
    )
    refuse_second_external_gate_at_fire(
        purpose="review",
        parent_thread="10128",
        thread_id="10128",
        request_id="req-b1",
    )


def test_refuse_external_gate_idle_operator_seat_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [],
            "seat_rows": [
                {
                    "registration_id": "a0e50b34",
                    "parent_thread": "12286",
                    "purpose": "operator-proxy",
                    "seat_bound_at": 100.0,
                }
            ],
            "observed_at": "2026-10-02T20:18:00+00:00",
        },
    )
    with pytest.raises(FrontierEndpointError) as exc:
        refuse_second_external_gate_at_fire(
            purpose="operator-proxy",
            parent_thread="12286",
            thread_id="12286",
            request_id="req-r-replay",
            mission_kind="root",
        )
    assert exc.value.code == "operator_seat_held"
    assert exc.value.status_code == 409


def test_hop_from_live_own_generate_exempts_sole_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hop excludes the caller's own generate; a harvested row is not required."""
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    caller = "96d6a7df-6a58-4255-9b2a-fe72d7b6d6cd"
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [
                {
                    "execution_id": caller,
                    "parent_thread": "12286",
                    "status": "running",
                    "stream_state": "running",
                    "purpose": "operator-proxy",
                    "registration_id": "reg-caller",
                }
            ],
            "seat_rows": [],
        },
    )
    refuse_second_external_gate_at_fire(
        purpose="operator-proxy",
        parent_thread="12286",
        thread_id="12286",
        request_id="req-hop-own",
        exclude_execution_id="new-exec-not-in-snap",
        hop_own_generate=True,
        predecessor_registration_id="reg-caller",
    )


def test_hop_exempt_does_not_clear_a_second_live_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Excluding the caller still refuses when another live gate occupies the lane."""
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    caller = "96d6a7df-6a58-4255-9b2a-fe72d7b6d6cd"
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [
                {
                    "execution_id": caller,
                    "parent_thread": "12286",
                    "status": "running",
                    "stream_state": "running",
                    "purpose": "operator-proxy",
                    "registration_id": "reg-caller",
                },
                {
                    "execution_id": "successor-already-live",
                    "parent_thread": "12286",
                    "status": "running",
                    "stream_state": "running",
                    "purpose": "operator-proxy",
                    "registration_id": "reg-successor",
                },
            ],
            "seat_rows": [],
        },
    )
    with pytest.raises(FrontierEndpointError) as exc:
        refuse_second_external_gate_at_fire(
            purpose="operator-proxy",
            parent_thread="12286",
            thread_id="12286",
            request_id="req-hop-two",
            exclude_execution_id="new-exec-not-in-snap",
            hop_own_generate=True,
            predecessor_registration_id="reg-caller",
        )
    assert exc.value.code == "cdp_external_gate_live"


def test_hop_sole_live_gate_exempt_without_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No registration: the sole live gate on the lane is the caller's generate."""
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [
                {
                    "execution_id": "96d6a7df-6a58-4255-9b2a-fe72d7b6d6cd",
                    "parent_thread": "12286",
                    "status": "running",
                    "stream_state": "running",
                    "purpose": "operator-proxy",
                }
            ],
            "seat_rows": [],
        },
    )
    refuse_second_external_gate_at_fire(
        purpose="operator-proxy",
        parent_thread="12286",
        thread_id="12286",
        request_id="req-hop-sole",
        exclude_execution_id="new-exec-not-in-snap",
        hop_own_generate=True,
    )


def test_non_hop_does_not_exempt_own_generate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """predecessor_registration_id is ignored unless hop_own_generate is set."""
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [
                {
                    "execution_id": "exec-live-gate",
                    "parent_thread": "12286",
                    "status": "running",
                    "stream_state": "running",
                    "purpose": "operator-proxy",
                    "registration_id": "reg-caller",
                }
            ],
            "seat_rows": [],
        },
    )
    with pytest.raises(FrontierEndpointError) as exc:
        refuse_second_external_gate_at_fire(
            purpose="operator-proxy",
            parent_thread="12286",
            thread_id="12286",
            request_id="req-non-hop",
            predecessor_registration_id="reg-caller",
        )
    assert exc.value.code == "cdp_external_gate_live"


def test_refuse_generate_wire_miss_mission_kind_none_refuses_own_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """a:37182 live miss: hop fire without mission_kind=hop refuses own generate."""
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import (
        _refuse_external_gate_for_generate,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [
                {
                    "execution_id": "f96a2cac-98f5-45cf-b6a8-2bbbba6759d0",
                    "parent_thread": "12286",
                    "status": "running",
                    "stream_state": "running",
                    "purpose": "operator-proxy",
                    "registration_id": "c1caf180",
                }
            ],
            "seat_rows": [],
        },
    )
    with pytest.raises(FrontierEndpointError) as exc:
        _refuse_external_gate_for_generate(
            purpose="operator-proxy",
            parent_thread="12286",
            thread_id="12286",
            request_id="7442e2891b03",
            execution_id="new-hop-successor",
            mission_kind=None,
            predecessor_registration_id="c1caf180",
        )
    assert exc.value.code == "cdp_external_gate_live"


def _live_operator_proxy_snap() -> dict[str, Any]:
    return {
        "rows": [
            {
                "execution_id": "f96a2cac-98f5-45cf-b6a8-2bbbba6759d0",
                "parent_thread": "14394",
                "status": "running",
                "stream_state": "running",
                "purpose": "operator-proxy",
                "registration_id": "c1caf180",
            }
        ],
        "seat_rows": [],
    }


def _review_body_over_min() -> str:
    body = (
        "## Question\n"
        "Does the capture path drop frames?\n\n"
        "```diff\n"
        "+ kept = True\n"
        "```\n\n"
        "VERDICT: approve only if the fence covers the drop.\n"
    )
    pad = "context line.\n"
    while len(body.encode("utf-8")) < 512:
        body += pad
    return body


def _plant_cortex_staged_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    *,
    body: str = "stub staged prompt\n",
) -> str:
    """On-disk cortex prompt for mocked ``_stage_inputs`` that still runs digest."""
    from claude_bundles import cdp_model_endpoint_staging as staging

    cortex = tmp_path / "cortex"
    rel = "notes/system/ephemeral/prompt.md"
    path = cortex / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(staging, "cortex_files_root", lambda: cortex)
    return f"cortex://{rel}"


def _stub_cdp_generate_after_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from claude_bundles import cdp_model_endpoint_staging as staging

    from systems.frontier_consult import cdp_generate as mod

    def _ephemeral(execution_id: str):
        return (
            tmp_path
            / "notes/system/ephemeral/cdp-endpoint"
            / execution_id
        )

    monkeypatch.setattr(staging, "ephemeral_dir", _ephemeral)
    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "admit_and_post_pointer_turn", AsyncMock(return_value=2))
    monkeypatch.setattr(
        mod,
        "admit_handoff_dispatch",
        AsyncMock(return_value=MagicMock(reason="ok")),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {
            "handoff_status": "ok",
            "poll_hint": {"thread_id": "14394", "from_agent": "web-anthropic"},
        },
    )
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "record_cdp_admit", lambda **kw: None)

    async def _worker(**kwargs: Any) -> None:
        del kwargs

    monkeypatch.setattr(mod, "run_cdp_worker", _worker)


@pytest.mark.asyncio
async def test_ac12_second_session_gate_hop_exempt_code_review_clear(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Item 12 — second session=operator-proxy on a live parent_thread is 409.

    Breaks when a second generate on the same parent_thread is admitted
    (missing occupancy), when the 409 names a field other than session,
    when mission_kind=hop refuses its own registered generate, or when
    job=code-review takes the operator-lane 409.
    """
    from fastapi import Response

    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: _live_operator_proxy_snap(),
    )
    _stub_cdp_generate_after_gate(monkeypatch, tmp_path)

    second = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="14394",
        parent_thread="14394",
        prompt="Second gate.\n",
        session="operator-proxy",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await dispatch_cdp_generate(
            request_id="req-ac12-second",
            body=second,
            response=Response(),
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "cdp_external_gate_live"
    assert exc.value.field == "session"

    hop = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="14394",
        parent_thread="14394",
        prompt="Hop own generate.\n",
        session="operator-proxy",
        mission_kind="hop",
        predecessor_registration_id="c1caf180",
    )
    hop_response = Response()
    hop_result = await dispatch_cdp_generate(
        request_id="req-ac12-hop",
        body=hop,
        response=hop_response,
    )
    assert 200 <= hop_response.status_code < 300
    assert hop_result["execution_id"]

    review = TeamDispatchGenerateBody(
        op="generate",
        job="code-review",
        model="cdp/opus-5.5",
        dispatch_thread_id="14394",
        parent_thread="14394",
        prompt=_review_body_over_min(),
    )
    review_response = Response()
    review_result = await dispatch_cdp_generate(
        request_id="req-ac12-review",
        body=review,
        response=review_response,
    )
    assert review_response.status_code != 409
    assert 200 <= review_response.status_code < 300
    assert review_result["execution_id"]
    assert review_result.get("error", {}).get("code") != "cdp_external_gate_live"


def test_refuse_generate_mission_kind_hop_admits_own_live_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wired hop: mission_kind=hop + predecessor registration excludes own generate."""
    from systems.frontier_consult.cdp_generate import (
        _refuse_external_gate_for_generate,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [
                {
                    "execution_id": "f96a2cac-98f5-45cf-b6a8-2bbbba6759d0",
                    "parent_thread": "12286",
                    "status": "running",
                    "stream_state": "running",
                    "purpose": "operator-proxy",
                    "registration_id": "c1caf180",
                }
            ],
            "seat_rows": [],
        },
    )
    _refuse_external_gate_for_generate(
        purpose="operator-proxy",
        parent_thread="12286",
        thread_id="12286",
        request_id="req-hop-wired",
        execution_id="new-hop-successor",
        mission_kind="hop",
        predecessor_registration_id="c1caf180",
    )


def test_hop_mismatched_registration_refuses_even_with_sole_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: mismatched predecessor_registration_id does not sole-gate exempt."""
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [
                {
                    "execution_id": "f96a2cac-98f5-45cf-b6a8-2bbbba6759d0",
                    "parent_thread": "12286",
                    "status": "running",
                    "stream_state": "running",
                    "purpose": "operator-proxy",
                    "registration_id": "c1caf180",
                }
            ],
            "seat_rows": [],
        },
    )
    with pytest.raises(FrontierEndpointError) as exc:
        refuse_second_external_gate_at_fire(
            purpose="operator-proxy",
            parent_thread="12286",
            thread_id="12286",
            request_id="req-hop-mismatch-reg",
            exclude_execution_id="new-hop-successor",
            hop_own_generate=True,
            predecessor_registration_id="reg-not-in-snap",
        )
    assert exc.value.code == "cdp_external_gate_live"


def test_refuse_second_external_gate_ignores_terminal_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L2-AC-c: terminal stream on lane does not block a second external gate admit."""
    from systems.frontier_consult.cdp_generate import (
        refuse_second_external_gate_at_fire,
    )

    exec_id = "exec-terminal-gate"
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: {
            "rows": [
                {
                    "execution_id": exec_id,
                    "parent_thread": "10128",
                    "status": "failed",
                    "stream_state": f"terminal:{exec_id}",
                    "purpose": "review",
                    "registration_id": "reg-terminal",
                }
            ],
        },
    )
    refuse_second_external_gate_at_fire(
        purpose="review",
        parent_thread="10128",
        thread_id="10128",
        request_id="req-terminal",
    )


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
async def test_dispatch_cdp_generate_forwards_parent_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """team_dispatch parent_thread reaches run_cdp_worker unchanged (T1 wire)."""
    from unittest.mock import AsyncMock, MagicMock

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: MagicMock(prompt_uri=prompt_uri, staged=True),
    )
    monkeypatch.setattr(mod, "admit_and_post_pointer_turn", AsyncMock(return_value=2))
    monkeypatch.setattr(
        mod,
        "admit_handoff_dispatch",
        AsyncMock(return_value=MagicMock(reason="ok")),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {
            "handoff_status": "ok",
            "poll_hint": {"thread_id": "1", "from_agent": "web-anthropic"},
        },
    )
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
        job="freeform",
        dispatch_thread_id="11165",
        model="cdp/opus-5-high",
        prompt="navigator doorbell",
        parent_thread="10479",
    )
    response = MagicMock()
    response.status_code = 202
    await mod.dispatch_cdp_generate(
        request_id="req-parent-thread",
        body=body,
        response=response,
    )
    assert pending
    await pending[0]
    assert captured
    assert captured[0]["parent_thread"] == "10479"


@pytest.mark.asyncio
async def test_dispatch_cdp_generate_worker_purpose_defaults_to_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Omitted session/purpose: worker stays None; explicit values pass through.

    Breaks when staging receives ask on admit (arch skill floor) or when explicit
    purpose/session fail to reach run_cdp_worker unchanged.
    """
    from unittest.mock import AsyncMock, MagicMock

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    staged_calls: list[dict[str, object]] = []

    def _stage_inputs(**kwargs: object) -> MagicMock:
        staged_calls.append(dict(kwargs))
        return MagicMock(prompt_uri=prompt_uri, staged=True)

    monkeypatch.setattr(mod, "_stage_inputs", _stage_inputs)
    monkeypatch.setattr(mod, "admit_and_post_pointer_turn", AsyncMock(return_value=2))
    monkeypatch.setattr(
        mod,
        "admit_handoff_dispatch",
        AsyncMock(return_value=MagicMock(reason="ok")),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {
            "handoff_status": "ok",
            "poll_hint": {"thread_id": "1", "from_agent": "web-anthropic"},
        },
    )
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "record_cdp_admit", lambda **kw: None)

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
        job="code-review",
        dispatch_thread_id="14638",
        model="cdp/opus-5.5",
        prompt=_review_body_over_min(),
    )
    response = MagicMock()
    response.status_code = 202
    await mod.dispatch_cdp_generate(
        request_id="req-purpose-default",
        body=body,
        response=response,
    )
    assert pending
    await pending[0]
    assert captured
    assert captured[0]["purpose"] is None
    assert staged_calls
    assert staged_calls[0]["purpose"] is None

    captured.clear()
    pending.clear()
    staged_calls.clear()

    body_explicit = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        dispatch_thread_id="14638",
        model="cdp/opus-5.5",
        prompt="Explicit purpose.\n",
        purpose="ask",
    )
    await mod.dispatch_cdp_generate(
        request_id="req-purpose-explicit",
        body=body_explicit,
        response=response,
    )
    assert pending
    await pending[0]
    assert captured[0]["purpose"] == "ask"
    assert staged_calls[0]["purpose"] == "ask"

    captured.clear()
    pending.clear()
    staged_calls.clear()

    body_session = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        dispatch_thread_id="14638",
        model="cdp/opus-5.5",
        prompt="Session bound.\n",
        session="produce",
    )
    await mod.dispatch_cdp_generate(
        request_id="req-purpose-session",
        body=body_session,
        response=response,
    )
    assert pending
    await pending[0]
    assert captured[0]["purpose"] == "produce"
    assert staged_calls[0]["purpose"] == "produce"


@pytest.mark.asyncio
async def test_dispatch_cdp_generate_forwards_generation_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """AC-S1-a: generation_options harvest knobs reach run_cdp_worker."""
    from unittest.mock import AsyncMock, MagicMock

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: MagicMock(prompt_uri=prompt_uri, staged=True),
    )
    monkeypatch.setattr(mod, "admit_and_post_pointer_turn", AsyncMock(return_value=2))
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {
            "handoff_status": "ok",
            "poll_hint": {"thread_id": "1", "from_agent": "web-anthropic"},
        },
    )
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
        job="freeform",
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


def test_format_cdp_result_body_failed_carries_envelope_fields() -> None:
    """L1-AC-c: FAILED body carries thread_id, pointer_turn, dispatch_link, cse."""
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="abcdef0123456789",
        satellite_execution_id="sat-env",
        prompt_uri="cortex://notes/system/threads/r-prompt.md",
        picker_model="fable-5",
        stall_stage="wall_clock_exceeded",
        error="CDP generate exceeded max_wall_s",
        extras={
            "thread_id": "10142",
            "pointer_turn": 7,
            "dispatch_link": "terminated:failed",
            "registration_id": "reg-abc",
            "chat_url": "https://claude.ai/cowork/cse_xyz",
        },
    )
    text = format_cdp_result_body(result)
    assert "- thread_id: `10142`" in text
    assert "- pointer_turn: `7`" in text
    assert "- dispatch_link: terminated:failed" in text
    assert "reg-abc" in text
    assert "https://claude.ai/cowork/cse_xyz" in text
    assert "# CDP generate FAILED" in text


@pytest.fixture
def _cdp_bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Hermetic agent-bus ASGI transport for CDP admit integration (L1-AC-a)."""
    import httpx
    from agent_bus_store import create_app
    from agent_bus_store.auth import require_token
    from agent_bus_store.db import init_db

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "1")
    init_db()
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    transport = httpx.ASGITransport(app=app)

    def _client_factory(_url: str, *, timeout: float = 10.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=timeout
        )

    monkeypatch.setattr(
        "systems.frontier_consult.handoff.make_async_client",
        _client_factory,
    )
    return transport


@pytest.mark.asyncio
async def test_cdp_admit_registers_dispatch_link_row(
    _cdp_bus_env, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """L1-AC-a: CDP admit registers dispatch link row (GET /dispatch-links → 200)."""
    import httpx
    from agent_bus_store.db import create_thread

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    row = create_thread(
        thread_id=None, slug="cdp-admit-link", lifecycle_state="pending"
    )
    assert row is not None
    thread_id = row["id"]

    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: type(
            "Staged",
            (),
            {"prompt_uri": prompt_uri, "staged": True},
        )(),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {
            "handoff_status": "ok",
            "poll_hint": {"thread_id": thread_id, "from_agent": "web-anthropic"},
        },
    )
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

    monkeypatch.setattr(mod, "run_cdp_worker", _fake_worker)

    def _capture_task(coro: object, **_kw: object) -> _FakeTask:
        pending.append(coro)
        return _FakeTask()

    monkeypatch.setattr(mod.asyncio, "create_task", _capture_task)

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        dispatch_thread_id=str(thread_id),
        model="cdp/fable",
        prompt="consult",
    )
    response = MagicMock()
    response.status_code = 202
    await mod.dispatch_cdp_generate(
        request_id="req-admit-link",
        body=body,
        response=response,
    )
    assert pending
    await pending[0]
    assert captured
    execution_id = str(captured[0]["execution_id"])

    async with httpx.AsyncClient(
        transport=_cdp_bus_env, base_url="http://test"
    ) as client:
        resp = await client.get(f"/dispatch-links/{execution_id}")
    assert resp.status_code == 200, resp.text
    link = resp.json()
    assert link["thread_id"] == thread_id
    assert link["pipeline_id"] == "cdp-generate"
    assert link["terminal_status"] is None


_SPECIMEN_SIDECAR = (
    "workspaces://universal-llm-gateway/tasks/review-packets/"
    "a38142-stream-capture-code-review.md"
)


def _plant_sidecar(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, body: str) -> None:
    from claude_bundles import cdp_model_endpoint_staging as staging

    ws = tmp_path / "ws"
    rel = _SPECIMEN_SIDECAR.removeprefix("workspaces://")
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(staging, "workspaces_root", lambda: ws)
    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path / "cortex")


@pytest.mark.asyncio
async def test_review_prompt_placeholder_specimen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """a:38326 — 13-byte `# placeholder` code-review sidecar is 422.

    Breaks when the gate runs after skill merge (byte count no longer 13),
    when too_small wins over the placeholder code, or when details omit the
    sidecar source.
    """
    from fastapi import Response

    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    _plant_sidecar(monkeypatch, tmp_path, "# placeholder")
    body = TeamDispatchGenerateBody(
        op="generate",
        job="code-review",
        model="cdp/opus-5.5",
        sidecar_ref=_SPECIMEN_SIDECAR,
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await dispatch_cdp_generate(
            request_id="req-review-placeholder",
            body=body,
            response=Response(),
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "review_prompt_placeholder"
    details = exc.value.details or {}
    assert details["prompt_byte_count"] == 13
    assert len(details["prompt_sha256"]) == 64
    assert details["source_ref"] == f"sidecar_ref={_SPECIMEN_SIDECAR}"
    assert "cortex://" in details["fix_hint"]


@pytest.mark.asyncio
async def test_review_prompt_staged_digest_on_admit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A real review packet admits 202 and echoes the staged file digest.

    Breaks when the receipt hashes the author body instead of the staged
    file, or when a body at the 512-byte floor is still refused.
    """
    import hashlib

    from claude_bundles.cdp_review_prompt_gate import read_cortex_uri_bytes
    from fastapi import Response

    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    _plant_sidecar(monkeypatch, tmp_path, _review_body_over_min())
    _stub_cdp_generate_after_gate(monkeypatch, tmp_path / "cortex")
    body = TeamDispatchGenerateBody(
        op="generate",
        job="code-review",
        model="cdp/opus-5.5",
        dispatch_thread_id="15426",
        sidecar_ref=_SPECIMEN_SIDECAR,
    )
    response = Response()
    result = await dispatch_cdp_generate(
        request_id="req-review-pass",
        body=body,
        response=response,
    )
    assert response.status_code == 202
    raw = read_cortex_uri_bytes(result["prompt_uri"])
    assert result["staged_prompt_byte_count"] == len(raw)
    assert result["staged_prompt_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["staged_prompt_byte_count"] >= 512


@pytest.mark.asyncio
async def test_staged_prompt_digest_on_freeform_admit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Non-review CDP generate still returns staged digest fields.

    Breaks when the digest is attached only for code-review contracts.
    """
    import hashlib

    from claude_bundles import cdp_model_endpoint_staging as staging
    from claude_bundles.cdp_review_prompt_gate import read_cortex_uri_bytes
    from fastapi import Response

    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    cortex = tmp_path / "cortex"
    monkeypatch.setattr(staging, "cortex_files_root", lambda: cortex)
    _stub_cdp_generate_after_gate(monkeypatch, cortex)
    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="15426",
        prompt="short freeform prompt\n",
    )
    response = Response()
    result = await dispatch_cdp_generate(
        request_id="req-freeform-digest",
        body=body,
        response=response,
    )
    assert response.status_code == 202
    raw = read_cortex_uri_bytes(result["prompt_uri"])
    assert result["staged_prompt_byte_count"] == len(raw)
    assert result["staged_prompt_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.asyncio
async def test_cdp_generate_admit_receipt_names_reroute_substrate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """202 and cdp.generate.admitted name cursor-sdk-reroute when resolve matches.

    Breaks when admit fires before resolve, when 202 still says web-anthropic-cdp
    under reroute, or when admitted substrate diverges from the 202 receipt.
    """
    import asyncio
    from datetime import UTC, datetime, timedelta

    import yaml
    from claude_bundles.cdp_model_endpoint import CDP_SUBSTRATE
    from fastapi import Response

    from systems.frontier_consult import cdp_generate_worker
    from systems.frontier_consult.cdp_events import CdpGenerateAdmitted
    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    future = (
        (datetime.now(UTC) + timedelta(days=7))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    policy_path = tmp_path / "route_policy.yaml"
    policy_path.write_text(
        yaml.safe_dump(
            {
                "cdp_reroute": {
                    "enabled": True,
                    "expires_at": future,
                    "target_model": "cursor/gemini-4-argon",
                    "match_prefixes": ["cdp/opus", "cdp/fable"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_cursor_reroute.load_route_policy",
        lambda path=None: yaml.safe_load(policy_path.read_text(encoding="utf-8")),
    )

    published: list[tuple[Any, dict[str, Any]]] = []

    def _capture(factory: Any, **kwargs: Any) -> bool:
        published.append((factory, kwargs))
        return True

    monkeypatch.setattr(cdp_generate_worker, "publish_cdp_kwargs", _capture)

    cortex = tmp_path / "cortex"
    _stub_cdp_generate_after_gate(monkeypatch, cortex)

    from systems.frontier_consult import cdp_generate as gen_mod
    from systems.frontier_consult import cdp_generate_worker as worker_mod

    async def _real_worker(**kwargs: Any) -> None:
        await worker_mod.run_cdp_worker(**kwargs)

    monkeypatch.setattr(gen_mod, "run_cdp_worker", _real_worker)
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.maybe_expand_cdp_prompt",
        lambda **kw: kw["prompt_uri"],
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.finalize_cdp_generate",
        AsyncMock(),
    )
    reroute_result = CdpGenerateResult(
        ok=True,
        body="VERDICT: ship",
        execution_id="exec-reroute-admit",
        satellite_execution_id="sdk-admit",
        prompt_uri="cortex://notes/prompt.md",
        picker_model="gemini-4-argon",
        substrate="cursor-sdk-reroute",
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_cursor_reroute.run_cdp_reroute_via_cursor_sdk",
        AsyncMock(return_value=reroute_result),
    )

    worker_tasks: list[asyncio.Task[None]] = []
    orig_create_task = asyncio.create_task

    def _track_task(coro: Any, **kw: Any) -> asyncio.Task[None]:
        task = orig_create_task(coro, **kw)
        worker_tasks.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", _track_task)

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="18026",
        prompt="reroute admit substrate check\n",
    )
    response = Response()
    result = await dispatch_cdp_generate(
        request_id="req-reroute-admit-substrate",
        body=body,
        response=response,
    )
    for task in worker_tasks:
        await task

    assert response.status_code == 202
    assert result["substrate"] == "cursor-sdk-reroute"
    assert result["result_handle"]["substrate"] == "cursor-sdk-reroute"
    assert result["capabilities"]["substrate"] == "cursor-sdk-reroute"
    assert result["capabilities"]["tool_surface"] == "cdp"
    assert result["resolved_model"] == "cdp/opus-5.5"
    assert CDP_SUBSTRATE == "web-anthropic-cdp"

    admitted = [kw for fac, kw in published if fac is CdpGenerateAdmitted]
    assert len(admitted) == 1
    assert admitted[0]["substrate"] == "cursor-sdk-reroute"


def _utf8_body_of_byte_length(target: int, *, seed: str = "R") -> str:
    body = seed
    while len(body.encode("utf-8")) < target:
        body += "x"
    while len(body.encode("utf-8")) > target:
        body = body[:-1]
    assert len(body.encode("utf-8")) == target
    return body


@pytest.mark.asyncio
async def test_review_prompt_too_small_inline_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short inline ``prompt`` on code-review is 422 before bus admit.

    Breaks when the gate runs after staging, when too_small is not emitted for
    sub-512 UTF-8 bodies, or when admit_handoff_dispatch still runs.
    """
    from fastapi import Response

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    admit = AsyncMock(return_value=MagicMock(reason="ok"))
    monkeypatch.setattr(mod, "admit_handoff_dispatch", admit)
    body = TeamDispatchGenerateBody(
        op="generate",
        job="code-review",
        model="cdp/opus-5.5",
        prompt="Review the diff.\n",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await dispatch_cdp_generate(
            request_id="req-review-too-small",
            body=body,
            response=Response(),
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "review_prompt_too_small"
    admit.assert_not_called()


@pytest.mark.asyncio
async def test_review_prompt_forbids_no_repo_access_wording() -> None:
    """Admit refuses diff-only / no-repo-access review author bodies (K30)."""
    from fastapi import Response

    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        job="code-review",
        model="cdp/opus-5.5",
        dispatch_thread_id="16709",
        prompt=_utf8_body_of_byte_length(512)
        + "\nReason only from the diff; you have no repo access.\n",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await dispatch_cdp_generate(
            request_id="req-review-no-repo",
            body=body,
            response=Response(),
        )
    assert exc.value.code == "review_prompt_no_repo_access_forbidden"


@pytest.mark.asyncio
async def test_review_prompt_delivery_review_contract() -> None:
    """delivery-review uses the same admit gate as code-review.

    Breaks when delivery-review skips enforce_review_prompt_gate.
    """
    from fastapi import Response

    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        job="delivery-review",
        model="cdp/opus-5.5",
        prompt="# placeholder",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await dispatch_cdp_generate(
            request_id="req-delivery-placeholder",
            body=body,
            response=Response(),
        )
    assert exc.value.code == "review_prompt_placeholder"


@pytest.mark.asyncio
async def test_review_prompt_byte_floor_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """511 UTF-8 bytes refused; 512 bytes admit with digest echo.

    Breaks when the floor uses character count instead of UTF-8 bytes or when
    digest runs after admit.
    """
    from fastapi import Response

    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    _stub_cdp_generate_after_gate(monkeypatch, tmp_path)
    too_small = TeamDispatchGenerateBody(
        op="generate",
        job="code-review",
        model="cdp/opus-5.5",
        dispatch_thread_id="15436",
        prompt=_utf8_body_of_byte_length(511),
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await dispatch_cdp_generate(
            request_id="req-review-511",
            body=too_small,
            response=Response(),
        )
    assert exc.value.code == "review_prompt_too_small"

    at_floor = TeamDispatchGenerateBody(
        op="generate",
        job="code-review",
        model="cdp/opus-5.5",
        dispatch_thread_id="15436",
        prompt=_utf8_body_of_byte_length(512),
    )
    response = Response()
    result = await dispatch_cdp_generate(
        request_id="req-review-512",
        body=at_floor,
        response=response,
    )
    assert response.status_code == 202
    assert result["staged_prompt_byte_count"] >= 512


def test_review_prompt_gate_matches_cortex_packet_over_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """cortex:// packet_path outranks workspaces sidecar for gate resolution.

    Breaks when the gate reads the sidecar while staging would stage the cortex
    packet (placeholder bypass). HTTP bodies forbid both fields; gate/staging
    share the same resolver for the combined shape.
    """
    from claude_bundles import cdp_model_endpoint_staging as staging
    from claude_bundles.cdp_model_endpoint_staging import CdpStagingError
    from claude_bundles.cdp_review_prompt_gate import enforce_review_prompt_gate

    cortex = tmp_path / "cortex"
    packet_uri = "cortex://notes/system/review-packets/placeholder-packet"
    rel = packet_uri.removeprefix("cortex://")
    (cortex / rel).parent.mkdir(parents=True, exist_ok=True)
    (cortex / rel).write_text("# placeholder", encoding="utf-8")
    monkeypatch.setattr(staging, "cortex_files_root", lambda: cortex)
    _plant_sidecar(monkeypatch, tmp_path, _review_body_over_min())

    with pytest.raises(CdpStagingError) as exc:
        enforce_review_prompt_gate(
            contract="code-review",
            prompt_text=None,
            packet_path=packet_uri,
            sidecar_ref=_SPECIMEN_SIDECAR,
        )
    assert exc.value.code == "review_prompt_placeholder"
    assert (exc.value.details or {})["source_ref"].startswith("prompt_uri=")


@pytest.mark.asyncio
async def test_staged_digest_failure_before_admit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Missing staged prompt file is 422 before admit (no duplicate CDP retry).

    Breaks when digest runs after poll_hint/worker creation or when a missing
    staged file returns 500 after partial admit.
    """
    from claude_bundles import cdp_model_endpoint_staging as staging
    from fastapi import Response

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    cortex = tmp_path / "cortex"
    cortex.mkdir()
    monkeypatch.setattr(staging, "cortex_files_root", lambda: cortex)
    admit = AsyncMock(return_value=MagicMock(reason="ok"))
    monkeypatch.setattr(mod, "admit_handoff_dispatch", admit)
    missing_uri = "cortex://notes/system/ephemeral/missing-prompt.md"
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: MagicMock(prompt_uri=missing_uri, staged=True),
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="15436",
        prompt="any prompt",
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await dispatch_cdp_generate(
            request_id="req-missing-staged-digest",
            body=body,
            response=Response(),
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "cdp_prompt_missing"
    admit.assert_not_called()


@pytest.mark.asyncio
async def test_supplied_thread_atomic_admit_and_post_no_separate_admit(
    _cdp_bus_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Supplied-thread path uses dispatch-admit-and-post, not post-then-admit."""
    from agent_bus_store.db import create_thread, insert_turn

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult import handoff
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    row = create_thread(thread_id=None, slug="parent", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    insert_turn(
        thread=thread_id,
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="prior",
        body="prior",
    )

    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: type("Staged", (), {"prompt_uri": prompt_uri, "staged": True})(),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {"handoff_status": "ok", "poll_hint": {}},
    )
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "run_cdp_worker", AsyncMock())

    admit_post = AsyncMock(wraps=handoff.admit_and_post_pointer_turn)
    monkeypatch.setattr(mod, "admit_and_post_pointer_turn", admit_post)
    separate_admit = AsyncMock(wraps=handoff.admit_handoff_dispatch)
    monkeypatch.setattr(mod, "admit_handoff_dispatch", separate_admit)

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/fable",
        dispatch_thread_id=str(thread_id),
        prompt="consult",
    )
    response = MagicMock()
    response.status_code = 202
    await mod.dispatch_cdp_generate(
        request_id="req-atomic-admit-post",
        body=body,
        response=response,
    )
    admit_post.assert_awaited_once()
    separate_admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_supplied_thread_timeout_retry_call_count(
    _cdp_bus_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Mocked admit timeout then retry invokes admit twice (same execution_id)."""
    import httpx
    from agent_bus_store.db import create_thread, get_turns

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult import handoff
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    row = create_thread(thread_id=None, slug="retry", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: type("Staged", (), {"prompt_uri": prompt_uri, "staged": True})(),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {"handoff_status": "ok", "poll_hint": {}},
    )
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "run_cdp_worker", AsyncMock())

    calls = {"n": 0}

    async def _flaky_admit(**kwargs: Any) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise FrontierEndpointError(
                request_id="req-timeout-retry",
                field="thread",
                reason="ReadTimeout: read timed out",
                status_code=503,
                code="agent_bus_unreachable",
            ) from httpx.ReadTimeout("read timed out")
        return await handoff.admit_and_post_pointer_turn(**kwargs)

    monkeypatch.setattr(mod, "admit_and_post_pointer_turn", _flaky_admit)

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/fable",
        dispatch_thread_id=str(thread_id),
        prompt="consult",
    )
    response = MagicMock()
    response.status_code = 202
    await mod.dispatch_cdp_generate(
        request_id="req-timeout-retry",
        body=body,
        response=response,
    )
    assert calls["n"] == 2
    turns = get_turns(thread=thread_id)
    pointer = [
        t for t in turns if "CDP generate admitted" in (t.get("body") or "")
    ]
    assert len(pointer) == 1


@pytest.mark.asyncio
async def test_supplied_thread_read_timeout_retries_one_link(
    _cdp_bus_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """First POST ReadTimeout before commit is retried; one link remains."""
    import httpx
    from agent_bus_store.db import create_thread, get_thread_with_links, get_turns

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult import handoff
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    row = create_thread(thread_id=None, slug="read-timeout", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: type("Staged", (), {"prompt_uri": prompt_uri, "staged": True})(),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {"handoff_status": "ok", "poll_hint": {}},
    )
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "run_cdp_worker", AsyncMock())

    posts = {"n": 0}
    inner = _cdp_bus_env

    class _FlakyTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "dispatch-admit-and-post" in str(
                request.url
            ):
                posts["n"] += 1
                if posts["n"] == 1:
                    raise httpx.ReadTimeout("read timed out", request=request)
            return await inner.handle_async_request(request)

    def _client_factory(_url: str, *, timeout: float = 10.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=_FlakyTransport(), base_url="http://test", timeout=timeout
        )

    monkeypatch.setattr(handoff, "make_async_client", _client_factory)

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/fable",
        dispatch_thread_id=str(thread_id),
        prompt="consult",
    )
    response = MagicMock()
    response.status_code = 202
    result = await mod.dispatch_cdp_generate(
        request_id="req-read-timeout-retry",
        body=body,
        response=response,
    )
    assert posts["n"] == 2
    execution_id = result["execution_id"]
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert len(detail["dispatch_links"]) == 1
    assert detail["dispatch_links"][0]["execution_id"] == execution_id
    turns = get_turns(thread=thread_id)
    pointer = [
        t for t in turns if "CDP generate admitted" in (t.get("body") or "")
    ]
    assert len(pointer) == 1


@pytest.mark.asyncio
async def test_supplied_thread_read_timeout_after_commit_one_pointer_turn(
    _cdp_bus_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Bus commits on POST #1; lost response retries idempotently to one pointer."""
    import httpx
    from agent_bus_store.db import create_thread, get_thread_with_links, get_turns

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult import handoff
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    row = create_thread(thread_id=None, slug="commit-timeout", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: type("Staged", (), {"prompt_uri": prompt_uri, "staged": True})(),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    captured: dict[str, int] = {}

    def _capture_handoff(**kw: Any) -> dict[str, str]:
        captured["after_turn"] = int(kw["after_turn"])
        return {"handoff_status": "ok", "poll_hint": {}}

    monkeypatch.setattr(mod, "build_handoff_result", _capture_handoff)
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "run_cdp_worker", AsyncMock())

    posts = {"n": 0}
    inner = _cdp_bus_env

    class _FlakyTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "dispatch-admit-and-post" in str(
                request.url
            ):
                posts["n"] += 1
                if posts["n"] == 1:
                    await inner.handle_async_request(request)
                    raise httpx.ReadTimeout("read timed out", request=request)
            return await inner.handle_async_request(request)

    def _client_factory(_url: str, *, timeout: float = 10.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=_FlakyTransport(), base_url="http://test", timeout=timeout
        )

    monkeypatch.setattr(handoff, "make_async_client", _client_factory)

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/fable",
        dispatch_thread_id=str(thread_id),
        prompt="consult",
    )
    response = MagicMock()
    response.status_code = 202
    await mod.dispatch_cdp_generate(
        request_id="req-commit-timeout-retry",
        body=body,
        response=response,
    )
    assert posts["n"] == 2
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert len(detail["dispatch_links"]) == 1
    turns = get_turns(thread=thread_id)
    pointer = [
        t for t in turns if "CDP generate admitted" in (t.get("body") or "")
    ]
    assert len(pointer) == 1
    pointer_turn = int(pointer[0]["turn_number"])
    assert captured["after_turn"] == pointer_turn


@pytest.mark.asyncio
async def test_supplied_thread_after_turn_is_pointer_turn(
    _cdp_bus_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Prior turns: admit-and-post return is the pointer turn, not 0."""
    import httpx
    from agent_bus_store.db import create_thread, get_turns, insert_turn

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult import handoff
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    row = create_thread(thread_id=None, slug="prior-turns", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    for n in range(1, 4):
        insert_turn(
            thread=thread_id,
            from_agent="dispatch",
            to_agent="web-anthropic",
            subject=f"prior {n}",
            body=f"prior body {n}",
        )
    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: type("Staged", (), {"prompt_uri": prompt_uri, "staged": True})(),
    )
    monkeypatch.setattr(mod, "upsert_inflight_leg", lambda **kw: None)
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    captured: dict[str, int] = {}

    def _capture_handoff(**kw: Any) -> dict[str, str]:
        captured["after_turn"] = int(kw["after_turn"])
        return {"handoff_status": "ok", "poll_hint": {}}

    monkeypatch.setattr(mod, "build_handoff_result", _capture_handoff)
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "run_cdp_worker", AsyncMock())

    inner = _cdp_bus_env

    class _TurnsGetFails(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/turns":
                return httpx.Response(500, json={"detail": "turns unavailable"})
            return await inner.handle_async_request(request)

    def _client_factory(_url: str, *, timeout: float = 10.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=_TurnsGetFails(), base_url="http://test", timeout=timeout
        )

    monkeypatch.setattr(handoff, "make_async_client", _client_factory)

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/fable",
        dispatch_thread_id=str(thread_id),
        prompt="consult",
    )
    response = MagicMock()
    response.status_code = 202
    await mod.dispatch_cdp_generate(
        request_id="req-after-turn",
        body=body,
        response=response,
    )
    turns = get_turns(thread=thread_id)
    pointer = [
        t for t in turns if "CDP generate admitted" in (t.get("body") or "")
    ]
    assert len(pointer) == 1
    assert captured["after_turn"] == int(pointer[0]["turn_number"])
    assert captured["after_turn"] > 1


@pytest.mark.asyncio
async def test_terminate_false_is_logged(
    _cdp_bus_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """terminate_handoff_dispatch False is logged, not discarded."""
    import logging

    from agent_bus_store.db import create_thread

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    row = create_thread(thread_id=None, slug="term-false", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: type("Staged", (), {"prompt_uri": prompt_uri, "staged": True})(),
    )
    monkeypatch.setattr(
        mod,
        "upsert_inflight_leg",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("inflight boom")),
    )
    monkeypatch.setattr(
        mod, "terminate_handoff_dispatch", AsyncMock(return_value=False)
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/fable",
        dispatch_thread_id=str(thread_id),
        prompt="consult",
    )
    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError, match="inflight boom"):
        await mod.dispatch_cdp_generate(
            request_id="req-term-false",
            body=body,
            response=MagicMock(),
        )
    assert any(
        "terminate_handoff_dispatch returned False" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_post_admit_worker_spawn_failure_terminates_link_failed(
    _cdp_bus_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Raise after bus commit terminates dispatch link as failed."""
    import httpx
    from agent_bus_store.db import create_thread

    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    row = create_thread(thread_id=None, slug="fail-after", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    prompt_uri = _plant_cortex_staged_prompt(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: type("Staged", (), {"prompt_uri": prompt_uri, "staged": True})(),
    )
    monkeypatch.setattr(
        mod,
        "upsert_inflight_leg",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("inflight boom")),
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/fable",
        dispatch_thread_id=str(thread_id),
        prompt="consult",
    )
    response = MagicMock()
    with pytest.raises(RuntimeError, match="inflight boom"):
        await mod.dispatch_cdp_generate(
            request_id="req-fail-after-admit",
            body=body,
            response=response,
        )

    async with httpx.AsyncClient(
        transport=_cdp_bus_env, base_url="http://test"
    ) as client:
        thread_resp = await client.get(f"/threads/{thread_id}")
    links = thread_resp.json().get("dispatch_links")
    assert links is None  # ThreadDetail omits links — use lineage or DB
    from agent_bus_store.db import get_thread_with_links

    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert detail["dispatch_links"][0]["terminal_status"] == "failed"


@pytest.mark.asyncio
async def test_post_pointer_turn_transport_error_agent_bus_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bus transport failures surface agent_bus_unreachable, not persona_violation."""
    import httpx

    from systems.frontier_consult.handoff import post_pointer_turn

    class _FailingClient:
        async def __aenter__(self) -> _FailingClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        async def get(self, *args: Any, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

    def _fail_client(_url: str, *, timeout: float = 10.0) -> _FailingClient:
        return _FailingClient()

    monkeypatch.setattr(
        "systems.frontier_consult.handoff.make_async_client",
        _fail_client,
    )
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "1")

    with pytest.raises(FrontierEndpointError) as exc:
        await post_pointer_turn(
            request_id="req-transport",
            thread_id="123",
            to_agent="web-anthropic",
            subject="subj",
            pointer_body="body",
            caller_agent="dispatch",
        )
    assert exc.value.code == "agent_bus_unreachable"
    assert exc.value.code != "persona_violation"


@pytest.mark.asyncio
async def test_admit_and_post_pointer_turn_missing_pointer_turn_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale agent-bus without pointer_turn yields 0 and a warning log."""
    import logging

    import httpx

    from systems.frontier_consult.handoff import admit_and_post_pointer_turn

    class _StaleBusClient:
        async def __aenter__(self) -> _StaleBusClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return httpx.Response(200, json={"turn_number": 3})

    monkeypatch.setattr(
        "systems.frontier_consult.handoff.make_async_client",
        lambda _url, *, timeout=10.0: _StaleBusClient(),
    )
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "1")
    caplog.set_level(logging.WARNING)

    after_turn = await admit_and_post_pointer_turn(
        request_id="req-stale-bus",
        thread_id="123",
        to_agent="web-anthropic",
        subject="subj",
        pointer_body="body",
        caller_agent="dispatch",
        execution_id="exec-stale",
        pipeline_id="cdp-generate",
    )
    assert after_turn == 0
    assert any(
        "missing pointer_turn" in rec.message for rec in caplog.records
    )

