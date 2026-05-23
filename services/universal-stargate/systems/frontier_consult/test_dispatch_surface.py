"""Phase 5 surface + admission unit tests — dispatch-surface-split.

Covers:
- S1–S3, S5–S7: body-model validation (Pydantic discriminated union, extra=forbid)
- D3–D4: op="to_thread" admission fast-fail on missing/closed thread

S4 (bus-mode happy path) and E1 (transcript regression) are integration tests
that require a running agent-bus and model.  See GAPS section at module bottom.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from .route import (
    FrontierDispatchGenerateBody,
    FrontierDispatchToThreadBody,
    TeamDispatchGenerateBody,
    TeamDispatchToThreadBody,
    _normalize_op_body,
    team_router,
)
from .service import FrontierEndpointError, _verify_thread_writable

# ---------------------------------------------------------------------------
# S1 — direct mode: _normalize_op_body sets output_contract=inline, op=generate
# ---------------------------------------------------------------------------


def test_s1_generate_normalizes_to_inline() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="gatherer",
        messages=[{"role": "user", "content": "pong"}],
    )
    kwargs = _normalize_op_body(body)
    assert kwargs["output_contract"] == "inline"
    assert kwargs["op"] == "generate"
    assert kwargs["role"] == "gatherer"
    assert "target_thread" not in kwargs


def test_s1_to_thread_normalizes_to_thread_contract() -> None:
    body = TeamDispatchToThreadBody(
        op="to_thread",
        role="gatherer",
        thread="867",
        messages=[{"role": "user", "content": "pong"}],
    )
    kwargs = _normalize_op_body(body)
    assert kwargs["output_contract"] == "thread"
    assert kwargs["op"] == "to_thread"
    assert kwargs["target_thread"] == "867"


def test_to_thread_propagates_subject_as_reply_subject() -> None:
    """Caller-supplied subject lands on reply_subject for the on-behalf turn."""
    body = TeamDispatchToThreadBody(
        op="to_thread",
        role="reviewer",
        thread="1051",
        subject="Re: plan-promotion review",
        messages=[{"role": "user", "content": "x"}],
    )
    kwargs = _normalize_op_body(body)
    assert kwargs["reply_subject"] == "Re: plan-promotion review"


def test_to_thread_omits_reply_subject_when_unset() -> None:
    """No subject ⇒ no reply_subject (delivery handler auto-derives)."""
    body = TeamDispatchToThreadBody(
        op="to_thread",
        role="reviewer",
        thread="1051",
        messages=[{"role": "user", "content": "x"}],
    )
    kwargs = _normalize_op_body(body)
    assert "reply_subject" not in kwargs


# ---------------------------------------------------------------------------
# S2 — generate op rejects thread field (extra="forbid")
# ---------------------------------------------------------------------------


def test_s2_generate_body_rejects_thread_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        TeamDispatchGenerateBody(
            op="generate",
            role="gatherer",
            thread="867",  # type: ignore[call-arg]  # forbidden extra field
            messages=[{"role": "user", "content": "x"}],
        )
    errors = exc_info.value.errors()
    assert any(
        e.get("type") in ("extra_forbidden",) or "extra" in str(e) for e in errors
    )


# ---------------------------------------------------------------------------
# S3 — generate op rejects result_delivery (extra="forbid")
# ---------------------------------------------------------------------------


def test_s3_generate_body_rejects_result_delivery() -> None:
    with pytest.raises(ValidationError):
        TeamDispatchGenerateBody(
            op="generate",
            role="gatherer",
            messages=[{"role": "user", "content": "x"}],
            result_delivery={"bus_thread": "867"},  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# S5 — to_thread op requires thread field
# ---------------------------------------------------------------------------


def test_s5_to_thread_body_requires_thread() -> None:
    with pytest.raises(ValidationError) as exc_info:
        TeamDispatchToThreadBody(
            op="to_thread",
            role="gatherer",
            messages=[{"role": "user", "content": "x"}],
            # thread intentionally omitted
        )
    errors = exc_info.value.errors()
    assert any(e.get("loc") == ("thread",) for e in errors)


# ---------------------------------------------------------------------------
# S6 — op field is required (missing discriminator → FastAPI 422)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _team_app() -> FastAPI:
    """Minimal FastAPI app that mounts team_router for route validation tests.

    Validation (S6, S7) fires before the handler body runs, so get_proxy()
    is never called — no mocking needed.
    """
    app = FastAPI()
    app.include_router(team_router)
    return app


def test_s6_op_required(_team_app: FastAPI) -> None:
    client = TestClient(_team_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/dispatch",
        json={"role": "gatherer", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 422
    body = resp.json()
    # FastAPI surfaces discriminator errors under "detail"
    assert "detail" in body or "error" in body


# ---------------------------------------------------------------------------
# S7 — invalid op value → FastAPI 422
# ---------------------------------------------------------------------------


def test_s7_invalid_op_rejected(_team_app: FastAPI) -> None:
    client = TestClient(_team_app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/team/dispatch",
        json={
            "op": "invalid_op",
            "role": "gatherer",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# frontier_dispatch symmetric validation
# ---------------------------------------------------------------------------


def test_frontier_generate_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        FrontierDispatchGenerateBody(
            op="generate",
            model="openai/gpt-5.4",
            thread="867",  # type: ignore[call-arg]
            messages=[{"role": "user", "content": "x"}],
        )


def test_frontier_to_thread_requires_thread() -> None:
    with pytest.raises(ValidationError):
        FrontierDispatchToThreadBody(
            op="to_thread",
            model="openai/gpt-5.4",
            messages=[{"role": "user", "content": "x"}],
        )


# ---------------------------------------------------------------------------
# D3 — op="to_thread" fast-fails when thread is not found (404)
# ---------------------------------------------------------------------------


def _patch_bus(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.service.make_async_client",
        lambda *a, **k: httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ),
    )


@pytest.mark.asyncio
async def test_d3_missing_thread_raises_422(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    _patch_bus(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(FrontierEndpointError) as exc_info:
        await _verify_thread_writable(
            "999999999",
            request_id="req-d3",
            auth_token="",
        )

    err = exc_info.value
    assert err.status_code == 422
    assert err.field == "thread"
    assert "not found" in err.reason.lower() or "999999999" in err.reason


# ---------------------------------------------------------------------------
# D4 — op="to_thread" fast-fails when thread is closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d4_closed_thread_raises_422(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 42, "status": "closed"})

    _patch_bus(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(FrontierEndpointError) as exc_info:
        await _verify_thread_writable(
            "42",
            request_id="req-d4",
            auth_token="",
        )

    err = exc_info.value
    assert err.status_code == 422
    assert err.field == "thread"
    assert "closed" in err.reason.lower()


# ---------------------------------------------------------------------------
# D3/D4 complement — open thread does NOT raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d3_open_thread_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 42, "status": "open"})

    _patch_bus(monkeypatch, httpx.MockTransport(handler))

    # Must not raise
    await _verify_thread_writable("42", request_id="req-d3-ok", auth_token="")


# ---------------------------------------------------------------------------
# GAPS — integration tests requiring live services
# ---------------------------------------------------------------------------
#
# S4 — Bus-mode happy path (op="to_thread" end-to-end, on-behalf delivery):
#   Requires: running agent-bus + model dispatch.
#   Manual: POST /api/v1/team/dispatch {op="to_thread", role="gatherer",
#   thread=<open_thread_id>, messages=[...]}; poll /api/v1/pipelines/result
#   until status="completed"; verify thread has a Stargate-posted turn whose
#   body matches result.content and whose from=<role>; verify
#   thread_reply_observed_at is populated.
#
# D1 — Bus-mode terminal status reflects on-behalf POST mid-flight:
#   Requires: tracker introspection surface not currently exposed.
#   Manual: POST to_thread; poll /api/v1/pipelines/result immediately (should
#   see status="running"); after model completes, re-poll (should see
#   status="completed" with thread_reply_observed_at populated).
#
# D2 — Bus-mode POST failure demotes status to failed:
#   Manual: dispatch to_thread against an unreachable agent-bus; assert
#   terminal status="failed", error.code starts with "post_".
#   Note: the prior contract (D2 = "test agent that never posts") is no
#   longer achievable as a failure mode — system-on-behalf delivery posts
#   record.result.content deterministically regardless of model tool use.
#
# D5 — Cancel mid-flight is non-transactional:
#   Requires: slow-reply agent fixture + cancel endpoint.
#   Manual: dispatch to_thread; POST /api/v1/pipelines/<exec_id>/cancel after 1s;
#   assert terminal status="cancelled", cancellation_observed_at populated.
#
# E1 — Transcript regression (yesterday's failure mode structurally impossible):
#   Requires: live dispatch.  Unit-testable slice covered by D6 (output_short
#   suppressed) and D3/D4 (no spurious envelope); remaining assertions are live.
#   Manual: dispatch op="to_thread" with role="gatherer"; verify final poll result
#   has no "output_short" hint; verify thread has exactly one stargate-posted
#   reply turn (no metadata envelope).
