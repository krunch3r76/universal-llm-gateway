"""Offline tests for skill-suggest dispatch shim."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from implement_admission.closeout_models import EvidenceUris, ImplementCloseout
from implement_admission.spec import CloseoutStatus
from pydantic import ValidationError

from .admission import FrontierEndpointError
from .skill_suggest_dispatch import (
    SkillSuggestDispatchRequest,
    apply_required_skills_backstop,
    await_worker_ack,
    dispatch_skill_suggest,
    post_skill_suggest_dispatch,
)
from .skill_suggest_dispatch_helpers import (
    build_worker_message,
    parse_envelope_from_closeout,
    validate_skill_suggest_envelope,
)
from .skill_suggest_worker_waiter import WorkerWaitOutcome


def _default_candidates() -> list[dict]:
    return [
        {
            "slug": "foo",
            "description": "Foo skill",
            "trigger_short": "foo",
            "score": 2.0,
        },
        {"slug": "bar", "description": "Bar skill", "trigger_short": "bar", "score": 0},
    ]


def _patch_extended_candidates():
    return patch(
        "systems.frontier_consult.skill_suggest_dispatch._fetch_extended_candidates",
        new_callable=AsyncMock,
        return_value=(_default_candidates(), [], []),
    )


def _native_envelope(*, agent: str = "claude-cursor") -> dict:
    return {
        "agent": agent,
        "suggestions": [{"id": "agent_skill:foo", "slug": "foo"}],
        "count": 1,
        "omitted": [],
        "degraded_skills": [],
        "loaded_echo": [],
        "seat_preloaded": [],
        "ranker_status": "pending",
        "degraded": False,
    }


def _closeout_with_sidecar(sidecar_ref: str) -> str:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="ok",
        source_ref=sidecar_ref,
        evidence_uris=EvidenceUris(artifact_paths=[sidecar_ref]),
    )
    return json.dumps(closeout.model_dump(mode="json"), separators=(",", ":"))


@pytest.mark.offline
def test_build_worker_message_includes_lane_parity_params() -> None:
    candidates = [
        {"slug": "foo", "description": "Foo", "trigger_short": "foo", "score": 2.0},
        {"slug": "bar", "description": "Bar", "trigger_short": "bar", "score": 0},
    ]
    msg = build_worker_message(
        loaded=["a"],
        conversation_context="ctx",
        agent="claude-cursor",
        limit=5,
        all_candidates=candidates,
    )
    assert '"claude-cursor"' in msg
    assert "limit=5" not in msg or "LIMIT: 5" in msg
    assert "light-bounded" in msg
    assert '"stage_a_score": 0' in msg
    assert "skill_suggest(" not in msg


@pytest.mark.offline
def test_validate_envelope_rejects_three_key_subset() -> None:
    partial = {"agent": "claude-cursor", "suggestions": [], "count": 0}
    assert (
        validate_skill_suggest_envelope(partial, canonical_agent="claude-cursor")
        is False
    )


@pytest.mark.offline
def test_validate_envelope_rejects_wrong_seat() -> None:
    env = _native_envelope(agent="claude-web")
    assert (
        validate_skill_suggest_envelope(env, canonical_agent="claude-cursor") is False
    )


@pytest.mark.offline
def test_parse_envelope_from_closeout_sidecar_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar_ref = "workspaces://universal-llm-gateway/tmp/reviews/closeouts/d1.md"
    sidecar_path = tmp_path / "tmp/reviews/closeouts/d1.md"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    envelope = _native_envelope()
    sidecar_path.write_text(
        f"worker output\n```json\n{json.dumps(envelope)}\n```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "systems.frontier_consult.skill_suggest_dispatch_helpers.resolve_workspaces_sidecar",
        lambda ref, workspaces_root: sidecar_path if ref == sidecar_ref else None,
    )
    parsed = parse_envelope_from_closeout(
        _closeout_with_sidecar(sidecar_ref),
        canonical_agent="claude-cursor",
        workspaces_root=tmp_path,
    )
    assert parsed == envelope


@pytest.mark.offline
@pytest.mark.asyncio
async def test_intake_rejects_rerank_field() -> None:
    with pytest.raises(ValidationError):
        SkillSuggestDispatchRequest(
            agent="claude-cursor",
            loaded=[],
            rerank=True,  # type: ignore[call-arg]
        )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_intake_rejects_route_field() -> None:
    with pytest.raises(ValidationError):
        SkillSuggestDispatchRequest(
            agent="claude-cursor",
            loaded=[],
            route="worker",  # type: ignore[call-arg]
        )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_intake_rejects_invalid_limit() -> None:
    with pytest.raises(ValidationError):
        SkillSuggestDispatchRequest(agent="claude-cursor", loaded=[], limit=0)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_unknown_agent_422_before_dispatch() -> None:
    body = SkillSuggestDispatchRequest(agent="not-a-seat", loaded=[])
    with pytest.raises(HTTPException) as exc:
        await dispatch_skill_suggest(request_id="req1", body=body)
    assert exc.value.status_code == 422


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_prefer_worker_false_skips_worker_hop() -> None:
    body = SkillSuggestDispatchRequest(
        agent="claude-cursor",
        loaded=["git-posture"],
        prefer_worker=False,
    )
    fallback_payload = _native_envelope()
    fallback_payload["route"] = "fallback"

    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
        ) as dispatch,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value=fallback_payload,
        ) as fallback,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
        ),
    ):
        result = await dispatch_skill_suggest(request_id="req-pw-false", body=body)

    dispatch.assert_not_called()
    fallback.assert_awaited_once()
    assert result["route"] == "fallback"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_fallback_on_worker_idle_timeout() -> None:
    body = SkillSuggestDispatchRequest(
        agent="claude-cursor",
        loaded=[],
    )
    fallback_payload = _native_envelope()
    fallback_payload["route"] = "fallback"
    fallback_payload["dispatch_execution_id"] = None

    with (
        _patch_extended_candidates(),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            return_value={
                "execution_id": "exec-1",
                "thread_id": "2111",
                "dispatch_id": "d-1",
            },
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_ack",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_completion",
            new_callable=AsyncMock,
            return_value=WorkerWaitOutcome(kind="idle_timeout"),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.fetch_worker_closeout_body",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value=fallback_payload,
        ) as fallback,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
        ),
    ):
        result = await dispatch_skill_suggest(request_id="req2", body=body)

    fallback.assert_awaited_once()
    assert result["route"] == "fallback"
    assert result["ranker_status"] == "pending"
    assert result["degraded"] is True
    assert result["degraded_reason"] == "worker_idle_timeout"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_uses_light_bounded_contract() -> None:
    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=[])
    fallback_payload = _native_envelope()
    fallback_payload["route"] = "fallback"

    with (
        _patch_extended_candidates(),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            side_effect=FrontierEndpointError(
                request_id="req-test",
                field="contract",
                reason="rejected",
            ),
        ) as dispatch,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value=fallback_payload,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
        ),
    ):
        await dispatch_skill_suggest(request_id="req-contract", body=body)

    dispatch.assert_awaited_once()
    assert dispatch.await_args.kwargs["contract"] == "light-bounded"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_worker_path_returns_envelope() -> None:
    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=["x"])
    envelope = _native_envelope()
    sidecar_ref = "workspaces://universal-llm-gateway/tmp/reviews/closeouts/d2.md"

    with (
        _patch_extended_candidates(),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            return_value={
                "execution_id": "exec-2",
                "thread_id": "2112",
                "result_handle": {
                    "kind": "dual",
                    "execution_id": "exec-2",
                    "thread_id": "2112",
                    "durable": True,
                },
            },
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_ack",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_completion",
            new_callable=AsyncMock,
            return_value=WorkerWaitOutcome(kind="completed"),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.fetch_worker_closeout_body",
            new_callable=AsyncMock,
            return_value=_closeout_with_sidecar(sidecar_ref),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.parse_envelope_from_closeout",
            return_value=dict(envelope),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
        ),
    ):
        result = await dispatch_skill_suggest(request_id="req3", body=body)

    assert result["route"] == "worker"
    assert result["dispatch_execution_id"] == "exec-2"
    assert result["dispatch_durable"] is True
    assert result["count"] == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_hallucinated_slug_triggers_fallback() -> None:
    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=[])
    bad_envelope = _native_envelope()
    bad_envelope["suggestions"] = [{"id": "agent_skill:phantom", "slug": "phantom"}]
    bad_envelope["count"] = 1
    fallback_payload = _native_envelope()
    fallback_payload["route"] = "fallback"
    sidecar_ref = "workspaces://universal-llm-gateway/tmp/reviews/closeouts/d-hall.md"

    with (
        _patch_extended_candidates(),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            return_value={"execution_id": "exec-h", "thread_id": "2116"},
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_ack",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_completion",
            new_callable=AsyncMock,
            return_value=WorkerWaitOutcome(kind="completed"),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.fetch_worker_closeout_body",
            new_callable=AsyncMock,
            return_value=_closeout_with_sidecar(sidecar_ref),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.parse_envelope_from_closeout",
            return_value=bad_envelope,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value=fallback_payload,
        ) as fallback,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
        ),
    ):
        result = await dispatch_skill_suggest(request_id="req-hall", body=body)

    fallback.assert_awaited_once()
    assert result["route"] == "fallback"
    assert result["degraded"] is True
    assert result["degraded_reason"] == "hallucinated_slugs"
    assert result["ranker_status"] == "pending"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_empty_loaded_accepted_at_route() -> None:
    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=[])
    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_skill_suggest",
            new_callable=AsyncMock,
            return_value={
                **_native_envelope(),
                "route": "fallback",
                "dispatch_execution_id": None,
            },
        ) as dispatch,
    ):
        result = await post_skill_suggest_dispatch(body)
    dispatch.assert_awaited_once()
    assert result["loaded_echo"] == []


@pytest.mark.offline
@pytest.mark.asyncio
async def test_await_worker_ack_true_when_admitted() -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"status": "admitted"}

    class _Client:
        async def get(self, *_args: object, **_kwargs: object) -> _Resp:
            return _Resp()

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.make_async_client",
            return_value=_Client(),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.read_ledger_dispatch_row",
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.find_durable_terminal_event",
            return_value=None,
        ),
    ):
        assert (
            await await_worker_ack(
                thread_id="2111",
                execution_id="exec-ack",
                dispatch_id="d-ack",
            )
            is True
        )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_await_worker_ack_false_when_stays_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, str | None]:
            return {"status": "queued"}

    class _Client:
        async def get(self, *_args: object, **_kwargs: object) -> _Resp:
            return _Resp()

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    sleeps: list[float] = []

    async def _fast_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        "systems.frontier_consult.skill_suggest_dispatch.asyncio.sleep",
        _fast_sleep,
    )
    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.make_async_client",
            return_value=_Client(),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.read_ledger_dispatch_row",
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.find_durable_terminal_event",
            return_value=None,
        ),
    ):
        assert (
            await await_worker_ack(
                thread_id="2111",
                execution_id="exec-q",
                dispatch_id="d-q",
            )
            is False
        )
    assert sleeps


@pytest.mark.offline
@pytest.mark.asyncio
async def test_await_worker_ack_fail_fast_on_probe_error_without_ledger() -> None:
    class _Client:
        async def get(self, *_args: object, **_kwargs: object) -> None:
            raise httpx.ConnectError("down")

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.make_async_client",
            return_value=_Client(),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.read_ledger_dispatch_row",
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.find_durable_terminal_event",
            return_value=None,
        ),
    ):
        assert (
            await await_worker_ack(
                thread_id="2111",
                execution_id="exec-down",
                dispatch_id="d-down",
            )
            is False
        )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_worker_unreachable_fast_fails_to_fallback() -> None:
    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=[])
    fallback_payload = _native_envelope()
    fallback_payload["route"] = "fallback"
    degraded_events: list[object] = []

    def _capture_event(event: object) -> None:
        degraded_events.append(event)

    with (
        _patch_extended_candidates(),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            return_value={
                "execution_id": "exec-q",
                "thread_id": "2113",
                "status": "queued",
            },
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_ack",
            new_callable=AsyncMock,
            return_value=False,
        ) as ack_probe,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_completion",
            new_callable=AsyncMock,
        ) as await_completion,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value=fallback_payload,
        ) as fallback,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
            side_effect=_capture_event,
        ),
    ):
        result = await dispatch_skill_suggest(request_id="req-no-ack", body=body)

    ack_probe.assert_awaited_once()
    await_completion.assert_not_called()
    fallback.assert_awaited_once()
    assert result["route"] == "fallback"
    assert len(degraded_events) == 1
    assert degraded_events[0].payload["reason"] == "worker_unreachable"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_queued_ack_proceeds_to_event_waiter() -> None:
    body = SkillSuggestDispatchRequest(agent="claude-cursor", loaded=["x"])
    envelope = _native_envelope()
    sidecar_ref = "workspaces://universal-llm-gateway/tmp/reviews/closeouts/d3.md"

    with (
        _patch_extended_candidates(),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            return_value={
                "execution_id": "exec-q-ack",
                "thread_id": "2114",
                "status": "queued",
            },
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_ack",
            new_callable=AsyncMock,
            return_value=True,
        ) as ack_probe,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_completion",
            new_callable=AsyncMock,
            return_value=WorkerWaitOutcome(kind="completed"),
        ) as await_completion,
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.fetch_worker_closeout_body",
            new_callable=AsyncMock,
            return_value=_closeout_with_sidecar(sidecar_ref),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.parse_envelope_from_closeout",
            return_value=dict(envelope),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
        ),
    ):
        result = await dispatch_skill_suggest(request_id="req-ack", body=body)

    ack_probe.assert_awaited_once()
    await_completion.assert_awaited_once()
    assert result["route"] == "worker"


def _backstop_skill_entities() -> dict[str, dict]:
    return {
        "task:fastmcp-post-p0-followons": {
            "id": "task:fastmcp-post-p0-followons",
            "attributes": {
                "required_skills": [
                    "architecture-invariants",
                    "ulg-architecture",
                    "missing-uri-skill",
                ]
            },
        },
        "agent_skill:architecture-invariants": {
            "id": "agent_skill:architecture-invariants",
            "name": "architecture-invariants",
            "source_uri": (
                "workspaces://universal-llm-gateway/docs/agent-guides/skills/"
                "architecture-invariants.md"
            ),
        },
        "agent_skill:ulg-architecture": {
            "id": "agent_skill:ulg-architecture",
            "name": "ulg-architecture",
            "source_uri": (
                "workspaces://universal-llm-gateway/docs/agent-guides/skills/"
                "ulg-architecture.md"
            ),
        },
        "agent_skill:missing-uri-skill": {
            "id": "agent_skill:missing-uri-skill",
            "name": "missing-uri-skill",
            "source_uri": None,
            "attributes": {"skill_category": "test"},
        },
    }


class _CortexDispatchResp:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> object:
        return self._payload


class _BackstopCortexClient:
    def __init__(self, entities: dict[str, dict]) -> None:
        self.entities = entities
        self.post_calls: list[tuple[str, dict | None]] = []

    async def post(
        self, path: str, json: dict | None = None, **_kwargs: object
    ) -> _CortexDispatchResp:
        self.post_calls.append((path, json))
        if path != "/dispatch":
            return _CortexDispatchResp(404, {"error": "unexpected path"})
        assert json is not None
        entity_id = str(json.get("arguments", {}).get("entity_id") or "")
        entity = self.entities.get(entity_id)
        if entity is None:
            return _CortexDispatchResp(404, {"error": "not found"})
        return _CortexDispatchResp(200, entity)

    async def __aenter__(self) -> _BackstopCortexClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_backstop_pins_required_skills_when_ranker_fallback() -> None:
    base = _native_envelope()
    client = _BackstopCortexClient(_backstop_skill_entities())
    with patch(
        "systems.frontier_consult.skill_suggest_dispatch.make_async_client",
        return_value=client,
    ):
        result = await apply_required_skills_backstop(
            base,
            entity_ids=["task:fastmcp-post-p0-followons"],
            loaded=[],
        )
    slugs = {item["slug"] for item in result["suggestions"]}
    assert slugs >= {"architecture-invariants", "ulg-architecture"}
    for item in result["suggestions"]:
        if item["slug"] in {"architecture-invariants", "ulg-architecture"}:
            assert item["score"] >= 10.0
            assert item["reason"] == "required_skills_backstop"
            assert item["reason_source"] == "deterministic"
    assert result["ranker_status"] == "pending"
    assert result["count"] == len(result["suggestions"])


@pytest.mark.offline
@pytest.mark.asyncio
async def test_backstop_excludes_already_loaded_required_skill() -> None:
    base = _native_envelope()
    client = _BackstopCortexClient(_backstop_skill_entities())
    with patch(
        "systems.frontier_consult.skill_suggest_dispatch.make_async_client",
        return_value=client,
    ):
        result = await apply_required_skills_backstop(
            base,
            entity_ids=["task:fastmcp-post-p0-followons"],
            loaded=["architecture-invariants"],
        )
    slugs = {item["slug"] for item in result["suggestions"]}
    assert "architecture-invariants" not in slugs
    assert "ulg-architecture" in slugs


@pytest.mark.offline
@pytest.mark.asyncio
async def test_backstop_null_source_uri_goes_to_degraded_skills() -> None:
    base = _native_envelope()
    client = _BackstopCortexClient(_backstop_skill_entities())
    with patch(
        "systems.frontier_consult.skill_suggest_dispatch.make_async_client",
        return_value=client,
    ):
        result = await apply_required_skills_backstop(
            base,
            entity_ids=["task:fastmcp-post-p0-followons"],
            loaded=[],
        )
    degraded = [
        item
        for item in result["degraded_skills"]
        if item.get("id") == "agent_skill:missing-uri-skill"
    ]
    assert len(degraded) == 1
    assert degraded[0]["degraded"] is True
    assert degraded[0]["reason"] == "source_uri_null"
    suggestion_slugs = {item["slug"] for item in result["suggestions"]}
    assert "missing-uri-skill" not in suggestion_slugs


@pytest.mark.offline
@pytest.mark.asyncio
async def test_backstop_empty_entity_ids_is_noop() -> None:
    base = _native_envelope()
    unchanged = await apply_required_skills_backstop(
        base,
        entity_ids=None,
        loaded=[],
    )
    assert unchanged == base
    also_unchanged = await apply_required_skills_backstop(
        base,
        entity_ids=[],
        loaded=[],
    )
    assert also_unchanged == base


@pytest.mark.offline
@pytest.mark.asyncio
async def test_backstop_entity_fetch_failure_still_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    base = _native_envelope()

    class _FailingClient:
        async def post(self, *_args: object, **_kwargs: object) -> _CortexDispatchResp:
            return _CortexDispatchResp(503, {"error": "unavailable"})

        async def __aenter__(self) -> _FailingClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    with patch(
        "systems.frontier_consult.skill_suggest_dispatch.make_async_client",
        return_value=_FailingClient(),
    ):
        result = await apply_required_skills_backstop(
            base,
            entity_ids=["task:fastmcp-post-p0-followons"],
            loaded=[],
        )
    assert result["suggestions"] == base["suggestions"]
    assert any("entity_get rejected" in rec.message for rec in caplog.records)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_fallback_applies_backstop() -> None:
    body = SkillSuggestDispatchRequest(
        agent="claude-cursor",
        loaded=[],
        entity_ids=["task:fastmcp-post-p0-followons"],
        prefer_worker=False,
    )
    fallback_payload = _native_envelope()
    fallback_payload["route"] = "fallback"
    client = _BackstopCortexClient(_backstop_skill_entities())

    with (
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.run_fallback",
            new_callable=AsyncMock,
            return_value=fallback_payload,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.make_async_client",
            return_value=client,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
        ),
    ):
        result = await dispatch_skill_suggest(request_id="req-backstop-fb", body=body)

    slugs = {item["slug"] for item in result["suggestions"]}
    assert "architecture-invariants" in slugs
    assert result["count"] == len(result["suggestions"])


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_worker_path_applies_backstop() -> None:
    body = SkillSuggestDispatchRequest(
        agent="claude-cursor",
        loaded=[],
        entity_ids=["task:fastmcp-post-p0-followons"],
    )
    envelope = _native_envelope()
    sidecar_ref = "workspaces://universal-llm-gateway/tmp/reviews/closeouts/d4.md"
    client = _BackstopCortexClient(_backstop_skill_entities())

    with (
        _patch_extended_candidates(),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.dispatch_cursor_sdk_generate",
            new_callable=AsyncMock,
            return_value={
                "execution_id": "exec-b",
                "thread_id": "2115",
            },
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_ack",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.await_worker_completion",
            new_callable=AsyncMock,
            return_value=WorkerWaitOutcome(kind="completed"),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.fetch_worker_closeout_body",
            new_callable=AsyncMock,
            return_value=_closeout_with_sidecar(sidecar_ref),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.parse_envelope_from_closeout",
            return_value=dict(envelope),
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch.make_async_client",
            return_value=client,
        ),
        patch(
            "systems.frontier_consult.skill_suggest_dispatch._publish_event",
        ),
    ):
        result = await dispatch_skill_suggest(
            request_id="req-backstop-worker",
            body=body,
        )

    assert result["route"] == "worker"
    slugs = {item["slug"] for item in result["suggestions"]}
    assert "ulg-architecture" in slugs
    assert result["count"] == len(result["suggestions"])
