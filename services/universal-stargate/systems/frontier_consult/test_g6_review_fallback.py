"""Rebase a fable review leg onto the execution id the conductor is polling."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from systems.frontier_consult.cdp_generate_worker import (
    note_unadopted_review_fallback,
    rebase_review_fallback,
    run_cdp_worker,
)


def _result(**kwargs: object) -> CdpGenerateResult:
    base = dict(
        ok=False,
        body="",
        execution_id="exec-opus",
        satellite_execution_id=None,
        prompt_uri="cortex://prompt",
        picker_model="opus",
    )
    base.update(kwargs)
    return CdpGenerateResult(**base)  # type: ignore[arg-type]


def test_rebase_review_fallback_keeps_original_execution_id() -> None:
    fallback = _result(
        ok=True,
        body="VERDICT: approve",
        execution_id="exec-opus:fable",
        satellite_execution_id="sat-fable",
        picker_model="fable",
        stall_stage=None,
    )
    rebased = rebase_review_fallback(
        fallback,
        original_execution_id="exec-opus",
        original_model_id="cdp/opus-5",
        fallback_model="cdp/fable",
        primary_satellite_execution_id="sat-opus",
    )
    assert rebased.execution_id == "exec-opus"
    assert rebased.body == "VERDICT: approve"
    assert rebased.extras["review_fallback_from"] == "cdp/opus-5"
    assert rebased.extras["review_fallback_model"] == "cdp/fable"
    assert rebased.extras["review_fallback_execution_id"] == "exec-opus:fable"
    assert rebased.extras["review_primary_satellite_execution_id"] == "sat-opus"


@pytest.mark.asyncio
async def test_cdp_worker_delivery_review_high_triggers_fable_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opus-high delivery-review passes contract into review_fallback_model."""
    from systems.frontier_consult import cdp_generate_worker as worker

    finalize = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.finalize_cdp_generate",
        finalize,
    )
    monkeypatch.setattr(worker, "publish_cdp_kwargs", lambda *a, **k: None)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.attach_satellite_execution_id",
        lambda **kw: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.maybe_expand_cdp_prompt",
        lambda **kw: kw["prompt_uri"],
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_inflight_ledger.upsert_inflight_leg",
        lambda **kw: None,
    )

    generate_calls: list[str] = []

    def _fake_generate(**kwargs: object) -> CdpGenerateResult:
        model_id = str(kwargs["model_id"])
        execution_id = str(kwargs["execution_id"])
        generate_calls.append(model_id)
        if model_id == "cdp/opus-5.5-high":
            return _result(
                stall_stage="completed_without_proof",
                error="no proof",
                satellite_execution_id="sat-opus",
            )
        if model_id == "cdp/fable":
            return _result(
                ok=True,
                body="VERDICT: approve",
                execution_id=execution_id,
                satellite_execution_id="sat-fable",
                picker_model="fable",
                stall_stage=None,
            )
        raise AssertionError(f"unexpected model_id {model_id!r}")

    async def _sync_to_thread(fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        return fn(*args, **kwargs)

    monkeypatch.setattr(worker.asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(worker, "run_cdp_generate", _fake_generate)

    await run_cdp_worker(
        execution_id="exec-review-high",
        model_id="cdp/opus-5.5-high",
        thread_id="14685",
        caller_agent="dispatch",
        prompt_uri="cortex://notes/prompt.md",
        request_id="req-review-high",
        contract="delivery-review",
    )

    assert generate_calls == ["cdp/opus-5.5-high", "cdp/fable"]
    finalize.assert_awaited_once()
    final = finalize.await_args.kwargs["result"]
    assert final.execution_id == "exec-review-high"
    assert final.body == "VERDICT: approve"
    assert final.extras.get("review_fallback_model") == "cdp/fable"


@pytest.mark.asyncio
async def test_cdp_worker_freeform_opus_high_without_proof_does_not_call_fable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opus-high completed_without_proof with contract=freeform must not call cdp/fable.

    Breaks when run_cdp_worker substitutes or defaults the contract it passes
    into review_fallback_model (e.g. hard-codes delivery-review), or when
    review_fallback_model drops its contract gate. Dropping the contract kwarg
    is caught by test_cdp_worker_delivery_review_high_triggers_fable_fallback,
    not here: review_fallback_model defaults contract to None, which returns None.
    """
    from systems.frontier_consult import cdp_generate_worker as worker

    finalize = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.finalize_cdp_generate",
        finalize,
    )
    monkeypatch.setattr(worker, "publish_cdp_kwargs", lambda *a, **k: None)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.attach_satellite_execution_id",
        lambda **kw: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.maybe_expand_cdp_prompt",
        lambda **kw: kw["prompt_uri"],
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_inflight_ledger.upsert_inflight_leg",
        lambda **kw: None,
    )

    generate_calls: list[str] = []

    def _fake_generate(**kwargs: object) -> CdpGenerateResult:
        model_id = str(kwargs["model_id"])
        generate_calls.append(model_id)
        if model_id != "cdp/opus-5.5-high":
            raise AssertionError(f"unexpected model_id {model_id!r}")
        return _result(
            execution_id=str(kwargs["execution_id"]),
            stall_stage="completed_without_proof",
            error="no proof",
            satellite_execution_id="sat-opus",
        )

    async def _sync_to_thread(fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        return fn(*args, **kwargs)

    monkeypatch.setattr(worker.asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(worker, "run_cdp_generate", _fake_generate)

    await run_cdp_worker(
        execution_id="exec-freeform-high",
        model_id="cdp/opus-5.5-high",
        thread_id="14698",
        caller_agent="dispatch",
        prompt_uri="cortex://notes/prompt.md",
        request_id="req-freeform-high",
        contract="freeform",
    )

    assert generate_calls == ["cdp/opus-5.5-high"]
    finalize.assert_awaited_once()
    final = finalize.await_args.kwargs["result"]
    assert final.execution_id == "exec-freeform-high"
    assert final.stall_stage == "completed_without_proof"
    assert "review_fallback_model" not in final.extras


def test_empty_fable_body_is_not_adopted() -> None:
    primary = _result(
        stall_stage="completed_without_proof",
        error="no proof",
        satellite_execution_id="sat-opus",
    )
    kept = note_unadopted_review_fallback(
        primary,
        fallback_model="cdp/fable",
        fallback_execution_id="exec-opus:fable",
        stall_stage="completed_without_proof",
    )
    assert kept.execution_id == "exec-opus"
    assert kept.stall_stage == "completed_without_proof"
    assert kept.extras["review_fallback_attempted"] is True
    assert kept.body == ""
