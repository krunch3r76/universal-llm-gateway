"""Regression tests for CDP generate deliverable-attest ingress."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from claude_bundles.cdp_model_endpoint import CdpGenerateResult
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from universal_protocol.errors import ProtocolError

from systems.frontier_consult import cdp_generate_attest as attest_mod
from systems.frontier_consult import cdp_generate_reconcile as reconcile
from systems.frontier_consult.cdp_events import publish_horizon_unverifiable_once
from systems.frontier_consult.cdp_generate_attest import (
    AttestConflictError,
    AttestDeliverableRequest,
    attest_cdp_generate_deliverable,
)
from systems.frontier_consult.cdp_generate_inflight_ledger import mark_proof_emitted
from systems.frontier_consult.cdp_generate_reconcile import (
    finalize_cdp_generate,
    reset_cdp_generate_reconcile_for_tests,
    upsert_inflight_leg,
)
from systems.proxy.routers.api.providers_cdp import router as providers_cdp_router


@pytest.fixture(autouse=True)
def _reset_ledger() -> None:
    reset_cdp_generate_reconcile_for_tests()


@pytest.fixture
def cortex_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(attest_mod, "cortex_files_root", lambda: tmp_path)
    return tmp_path


def _write_proof_file(root: Path, rel: str, content: bytes) -> tuple[str, str]:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    return f"cortex://{rel}", digest


def _seed_open_leg(execution_id: str = "exec-attest-1") -> None:
    upsert_inflight_leg(
        execution_id=execution_id,
        request_id="req-attest-1",
        thread_id="9628",
        pointer_turn=2,
        caller_agent="dispatch",
        prompt_uri="cortex://notes/prompt.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id=execution_id,
        satellite_execution_id="sat-attest-1",
    )


def _publish_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    published: list[tuple[str, dict[str, Any]]] = []

    def _capture(factory: Any, **kwargs: Any) -> bool:
        published.append((factory.__name__, dict(kwargs)))
        return True

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    return published


@pytest.mark.asyncio
async def test_attest_matching_sha_emits_one_proof_and_delivers(
    monkeypatch: pytest.MonkeyPatch,
    cortex_root: Path,
) -> None:
    """AC (a) — matching sha on open leg yields one proof via=attest + bus turn."""
    _seed_open_leg()
    uri, digest = _write_proof_file(cortex_root, "notes/test/proof.md", b"proof-bytes")
    published = _publish_spy(monkeypatch)
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )

    result = await attest_cdp_generate_deliverable(
        execution_id="exec-attest-1",
        body=AttestDeliverableRequest(
            content_proof_uri=uri,
            written_sha256=digest,
            attested_by="test-seat",
        ),
    )

    proof_rows = [row for row in published if row[0] == "CdpGenerateProof"]
    assert len(proof_rows) == 1
    assert proof_rows[0][1]["via"] == "attest"
    assert proof_rows[0][1]["attested_by"] == "test-seat"
    assert "CdpGenerateReconciled" not in {name for name, _ in published}
    assert deliver.await_count == 1
    assert result.proof_emitted is True
    assert result.delivered is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("sha_mismatch", "cdp_attest_sha_mismatch"),
        ("missing_target", "cdp_attest_target_missing"),
        ("leg_missing", "cdp_attest_leg_not_open"),
    ],
)
async def test_attest_refusals_emit_zero_events(
    monkeypatch: pytest.MonkeyPatch,
    cortex_root: Path,
    setup: str,
    code: str,
) -> None:
    """AC (b) — sha/target/leg refusals raise ProtocolError with zero events."""
    published = _publish_spy(monkeypatch)
    body = AttestDeliverableRequest(
        content_proof_uri="cortex://notes/missing.md",
        written_sha256="0" * 64,
        attested_by="test-seat",
    )

    if setup == "sha_mismatch":
        _seed_open_leg()
        uri, digest = _write_proof_file(
            cortex_root, "notes/test/proof.md", b"proof-bytes"
        )
        body = AttestDeliverableRequest(
            content_proof_uri=uri,
            written_sha256="f" * 64,
            attested_by="test-seat",
        )
    elif setup == "missing_target":
        _seed_open_leg()
        body = AttestDeliverableRequest(
            content_proof_uri="cortex://notes/does-not-exist.md",
            written_sha256="0" * 64,
            attested_by="test-seat",
        )
    elif setup == "leg_missing":
        pass

    with pytest.raises(ProtocolError) as excinfo:
        await attest_cdp_generate_deliverable(
            execution_id="exec-attest-1",
            body=body,
        )
    assert excinfo.value.code == code
    assert published == []


@pytest.mark.asyncio
async def test_attest_conflict_when_proof_already_emitted(
    monkeypatch: pytest.MonkeyPatch,
    cortex_root: Path,
) -> None:
    """AC (b) — proof_emitted=1 yields 409 without filesystem read."""
    _seed_open_leg()
    uri, digest = _write_proof_file(cortex_root, "notes/test/proof.md", b"proof-bytes")
    mark_proof_emitted("exec-attest-1")

    def _forbidden_read(**kwargs: Any) -> Path:
        raise AssertionError(
            "filesystem read should not happen when proof already emitted"
        )

    monkeypatch.setattr(attest_mod, "_resolve_attest_file", _forbidden_read)
    _publish_spy(monkeypatch)

    with pytest.raises(AttestConflictError) as excinfo:
        await attest_cdp_generate_deliverable(
            execution_id="exec-attest-1",
            body=AttestDeliverableRequest(
                content_proof_uri=uri,
                written_sha256=digest,
                attested_by="test-seat",
            ),
        )
    assert excinfo.value.proof_emitted is True


@pytest.mark.asyncio
async def test_attest_then_worker_finalize_is_no_op(
    monkeypatch: pytest.MonkeyPatch,
    cortex_root: Path,
) -> None:
    """AC (c) — mission-retained leg attested; later worker finalize is a no-op."""
    _seed_open_leg()
    uri, digest = _write_proof_file(cortex_root, "notes/test/proof.md", b"proof-bytes")
    published = _publish_spy(monkeypatch)
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )

    await attest_cdp_generate_deliverable(
        execution_id="exec-attest-1",
        body=AttestDeliverableRequest(
            content_proof_uri=uri,
            written_sha256=digest,
            attested_by="test-seat",
        ),
    )

    worker_result = CdpGenerateResult(
        ok=True,
        body="worker harvest",
        execution_id="exec-attest-1",
        satellite_execution_id="sat-attest-1",
        prompt_uri="cortex://notes/prompt.md",
        picker_model="opus-5",
        content_proof_uri="cortex://notes/other.md",
    )
    await finalize_cdp_generate(
        result=worker_result,
        request_id="req-attest-1",
        thread_id="9628",
        to_agent="dispatch",
        pointer_turn=2,
        via="worker",
    )

    proof_rows = [row for row in published if row[0] == "CdpGenerateProof"]
    assert len(proof_rows) == 1
    assert proof_rows[0][1]["via"] == "attest"
    assert deliver.await_count == 1


def test_attest_request_model_rejects_archive_uri_only() -> None:
    """AC (d) — archive_uri-only body refused by request model."""
    with pytest.raises(ValidationError):
        AttestDeliverableRequest(
            archive_uri="cortex://notes/archive.md",
            written_sha256="0" * 64,
            attested_by="test-seat",
        )


def test_attest_route_rejects_extra_archive_uri_field() -> None:
    """AC (d) — extra archive_uri field refused at HTTP 422; handler not entered."""
    app = FastAPI()
    app.include_router(providers_cdp_router, prefix="/api/v1")
    client = TestClient(app)
    published: list[tuple[str, dict[str, Any]]] = []

    def _capture(factory: Any, **kwargs: Any) -> bool:
        published.append((factory.__name__, dict(kwargs)))
        return True

    import systems.frontier_consult.cdp_generate_reconcile as reconcile_mod

    original = reconcile_mod.publish_cdp_kwargs
    reconcile_mod.publish_cdp_kwargs = _capture  # type: ignore[assignment]
    try:
        response = client.post(
            "/api/v1/providers/cdp/generate/exec-attest-1/attest",
            json={
                "content_proof_uri": "cortex://notes/x.md",
                "written_sha256": "0" * 64,
                "attested_by": "test-seat",
                "archive_uri": "cortex://notes/archive.md",
            },
        )
    finally:
        reconcile_mod.publish_cdp_kwargs = original

    assert response.status_code == 422
    assert published == []


@pytest.mark.asyncio
async def test_attest_idempotent_second_call(
    monkeypatch: pytest.MonkeyPatch,
    cortex_root: Path,
) -> None:
    """AC (f) — second attest after proof_emitted yields conflict, one proof total."""
    _seed_open_leg()
    uri, digest = _write_proof_file(cortex_root, "notes/test/proof.md", b"proof-bytes")
    published = _publish_spy(monkeypatch)
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )
    body = AttestDeliverableRequest(
        content_proof_uri=uri,
        written_sha256=digest,
        attested_by="test-seat",
    )

    await attest_cdp_generate_deliverable(execution_id="exec-attest-1", body=body)
    with pytest.raises(AttestConflictError):
        await attest_cdp_generate_deliverable(execution_id="exec-attest-1", body=body)

    proof_rows = [row for row in published if row[0] == "CdpGenerateProof"]
    assert len(proof_rows) == 1
    assert deliver.await_count == 1


@pytest.mark.asyncio
async def test_attest_after_horizon_unverifiable_still_terminalizes(
    monkeypatch: pytest.MonkeyPatch,
    cortex_root: Path,
) -> None:
    """AC (g) — horizon.unverifiable retain does not block attest terminalization."""
    _seed_open_leg()
    publish_horizon_unverifiable_once(
        request_id="req-attest-1",
        execution_id="exec-attest-1",
        satellite_execution_id="sat-attest-1",
        thread_id="9628",
        stall_stage="horizon_unverifiable_retained",
        error="probe retained",
    )
    uri, digest = _write_proof_file(cortex_root, "notes/test/proof.md", b"proof-bytes")
    published = _publish_spy(monkeypatch)
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )

    result = await attest_cdp_generate_deliverable(
        execution_id="exec-attest-1",
        body=AttestDeliverableRequest(
            content_proof_uri=uri,
            written_sha256=digest,
            attested_by="test-seat",
        ),
    )

    leg = reconcile.read_inflight_leg("exec-attest-1")
    assert leg is not None
    assert leg.proof_emitted is True
    assert result.proof_emitted is True
    proof_rows = [row for row in published if row[0] == "CdpGenerateProof"]
    assert len(proof_rows) == 1
