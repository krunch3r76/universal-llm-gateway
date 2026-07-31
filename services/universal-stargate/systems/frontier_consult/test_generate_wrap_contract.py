"""Offline tests for contract=wrap on cursor-sdk generate (todo:generate-wrap-contract)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse
from implement_admission.preflight import DecisionNotAssertedError
from pydantic import ValidationError

from systems.frontier_consult.generate_wrap import (
    GenerateWrapResult,
    dispatch_cursor_sdk_generate_route,
    prepare_implement_packet,
)
from systems.frontier_consult.route import TeamDispatchGenerateBody, team_dispatch


def _wrap_result(**overrides: object) -> GenerateWrapResult:
    base = {
        "packet_path": "tmp/reviews/slug-implement-packet.md",
        "materialized": True,
        "implement_spec_hash": "abc123",
        "packet_sha256": "def456",
        "materialization_present": True,
        "warnings": [],
    }
    base.update(overrides)
    return GenerateWrapResult(**base)  # type: ignore[arg-type]


@pytest.mark.offline
def test_wrap_body_rejects_packet_path() -> None:
    with pytest.raises(ValidationError):
        TeamDispatchGenerateBody(
            op="generate",
            role="cursor-sdk",
            contract="wrap",
            source_ref="todo:slug",
            packet_path="tmp/reviews/packet.md",
        )


@pytest.mark.offline
def test_wrap_body_requires_source_ref() -> None:
    with pytest.raises(ValidationError):
        TeamDispatchGenerateBody(
            op="generate",
            role="cursor-sdk",
            contract="wrap",
        )


@pytest.mark.offline
def test_wrap_body_rejects_gating_misleading_knobs() -> None:
    with pytest.raises(ValidationError):
        TeamDispatchGenerateBody(
            op="generate",
            role="cursor-sdk",
            contract="wrap",
            source_ref="todo:slug",
            density_triage="judgment_required",
        )


@pytest.mark.offline
def test_wrap_body_allows_absent_dispatch_thread_id() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="cursor-sdk",
        contract="wrap",
        source_ref="todo:slug",
    )
    assert body.dispatch_thread_id is None


@pytest.mark.asyncio
@pytest.mark.offline
async def test_wrap_happy_path_returns_200_without_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_mock = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        sdk_mock,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        lambda **kwargs: _wrap_result(),
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        role="cursor-sdk",
        contract="wrap",
        source_ref="todo:generate-wrap-contract",
    )
    response = Response()
    result = await team_dispatch(body, response)

    assert response.status_code == 200
    assert result["contract"] == "wrap"
    assert result["status"] == "materialized"
    assert result["materialized"] is True
    assert result["materialization_mode"] == "auto"
    assert result["packet_path"] == "tmp/reviews/slug-implement-packet.md"
    assert result["implement_spec_hash"] == "abc123"
    assert result["packet_sha256"] == "def456"
    assert result["materialization_present"] is True
    assert "execution_id" not in result
    assert "poll_hint" not in result
    assert "resolved_model" not in result
    assert "capabilities" not in result
    sdk_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.offline
async def test_wrap_does_not_spawn_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_mock = AsyncMock(return_value={"execution_id": "exec-should-not-run"})
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        sdk_mock,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        lambda **kwargs: _wrap_result(),
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        role="cursor-sdk",
        contract="wrap",
        source_ref="todo:slug",
    )
    response = Response()
    result = await team_dispatch(body, response)

    assert response.status_code == 200
    assert "execution_id" not in result
    sdk_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.offline
async def test_wrap_gated_source_ref_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_mock = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        sdk_mock,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        lambda **kwargs: GenerateWrapResult(
            packet_path=None,
            gated=True,
            gated_reason="dense_spec_invalid",
        ),
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        role="cursor-sdk",
        contract="wrap",
        source_ref="todo:not-ready",
    )
    result = await team_dispatch(body, Response())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert b"generate_source_ref_gated" in result.body
    sdk_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.offline
async def test_wrap_decision_not_asserted_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_mock = AsyncMock()

    def _raise_decision(**kwargs: object) -> GenerateWrapResult:  # noqa: ARG001
        raise DecisionNotAssertedError()

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        sdk_mock,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        _raise_decision,
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        role="cursor-sdk",
        contract="wrap",
        source_ref="todo:unratified",
    )
    result = await team_dispatch(body, Response())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert b"decision_not_asserted" in result.body
    sdk_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.offline
async def test_wrap_role_not_admitted_for_non_sdk_role() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="reviewer",
        dispatch_thread_id="thread:arc",
        contract="wrap",
        source_ref="todo:slug",
    )
    result = await team_dispatch(body, Response())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert b"wrap_role_not_admitted" in result.body


@pytest.mark.asyncio
@pytest.mark.offline
async def test_wrap_packet_scheme_source_ref_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_refs: list[str] = []

    def _prepare(**kwargs: object) -> GenerateWrapResult:
        seen_refs.append(kwargs["source_ref"])  # type: ignore[arg-type]
        return _wrap_result(packet_path="tmp/reviews/from-packet-scheme.md")

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        _prepare,
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        role="cursor-sdk",
        contract="wrap",
        source_ref="packet:tmp/reviews/existing-packet.md",
    )
    response = Response()
    result = await team_dispatch(body, response)

    assert response.status_code == 200
    assert seen_refs == ["packet:tmp/reviews/existing-packet.md"]


@pytest.mark.asyncio
@pytest.mark.offline
async def test_wrap_route_defensive_packet_path_rejection() -> None:
    body = TeamDispatchGenerateBody.model_construct(
        op="generate",
        role="cursor-sdk",
        contract="wrap",
        source_ref="todo:slug",
        packet_path="tmp/reviews/packet.md",
    )
    result = await dispatch_cursor_sdk_generate_route(
        request_id="req-wrap",
        body=body,
        role="cursor-sdk",
        response=Response(),
    )

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert b"wrap_with_packet_path" in result.body


@pytest.mark.offline
def test_materialization_threads_bridge_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    from systems.frontier_consult.implement_admission_bridge import BridgeResult

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_implement_ready",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_decision_asserted",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_source_ref_to_packet",
        lambda *args, **kwargs: BridgeResult(
            gated=False,
            packet_path="tmp/reviews/materialized.md",
            implement_spec_hash="hash1",
            packet_sha256="sha1",
            materialization_present=True,
            warnings=["warn"],
        ),
    )

    result = prepare_implement_packet(
        request_id="req-prov",
        source_ref="todo:slug",
        packet_path=None,
        caller_agent=None,
        cortex=MagicMock(),
        workspaces_root=tmp_path,  # type: ignore[arg-type]
    )

    assert result.implement_spec_hash == "hash1"
    assert result.packet_sha256 == "sha1"
    assert result.materialization_present is True
