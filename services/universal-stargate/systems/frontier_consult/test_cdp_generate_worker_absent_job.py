"""AC11: an omitted hop job stays absent at run_cdp_worker.

HTTP 2xx, execution_id, and a missing call-site kwarg are not the
observable. The call is bound against the real signature with defaults
applied, and the value forwarded into maybe_expand_cdp_prompt is the
same parameter. Absent is None. The string \"none\" fails the test.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from systems.frontier_consult.cdp_generate_worker import run_cdp_worker


def _hop_body_job_omitted() -> SimpleNamespace:
    """Stargate hop body: mission_kind=hop and no job. Both wire names are None."""
    return SimpleNamespace(
        op="generate",
        model="cdp/opus-5",
        prompt="hop with job omitted",
        mission_kind="hop",
        contract=None,
        job=None,
        seat=None,
        role=None,
        dispatch_lane=None,
        dispatch_thread_id="11165",
        sidecar_ref=None,
        packet_path=None,
        skills=None,
        purpose=None,
        parent_thread=None,
        caller_agent="cursor",
        bus_lifecycle=None,
        timeout_seconds=None,
        generation_options=None,
        predecessor_registration_id=None,
        reasoning_effort=None,
    )


@pytest.mark.asyncio
async def test_absent_hop_job_does_not_reach_worker_as_contract_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult import cdp_generate_worker as worker_mod
    from systems.frontier_consult import prompt_expand_prelude as prelude

    monkeypatch.setattr(
        mod,
        "_stage_inputs",
        lambda **kw: MagicMock(
            prompt_uri="cortex://notes/system/ephemeral/prompt.md", staged=True
        ),
    )
    monkeypatch.setattr(mod, "post_pointer_turn", AsyncMock(return_value=2))
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
    monkeypatch.setattr(worker_mod, "publish_cdp_kwargs", lambda *a, **k: None)

    expanded: dict[str, Any] = {}

    def _record_expand(**kwargs: Any) -> str:
        expanded["contract"] = kwargs["contract"]
        return str(kwargs["prompt_uri"])

    def _fake_generate(**kwargs: Any) -> CdpGenerateResult:
        return CdpGenerateResult(
            ok=True,
            body="ok",
            execution_id=str(kwargs["execution_id"]),
            satellite_execution_id=None,
            prompt_uri=str(kwargs["prompt_uri"]),
            picker_model="opus-5",
        )

    monkeypatch.setattr(prelude, "maybe_expand_cdp_prompt", _record_expand)
    monkeypatch.setattr(worker_mod, "run_cdp_generate", _fake_generate)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.finalize_cdp_generate",
        AsyncMock(return_value=None),
    )

    observed: dict[str, Any] = {}
    pending: list[Any] = []
    real_sig = inspect.signature(run_cdp_worker)

    async def _spy(**kwargs: Any) -> None:
        bound = real_sig.bind(**kwargs)
        bound.apply_defaults()
        observed["bound_contract"] = bound.arguments["contract"]
        observed["call_had_contract_kwarg"] = "contract" in kwargs
        await run_cdp_worker(**kwargs)

    class _FakeTask:
        def add_done_callback(self, _cb: object) -> None:
            return None

    def _capture_task(coro: object, **kwargs: object) -> _FakeTask:
        pending.append(coro)
        return _FakeTask()

    monkeypatch.setattr(mod, "run_cdp_worker", _spy)
    monkeypatch.setattr(mod.asyncio, "create_task", _capture_task)

    response = MagicMock()
    response.status_code = 202
    await mod.dispatch_cdp_generate(
        request_id="req-absent-hop-job",
        body=_hop_body_job_omitted(),  # type: ignore[arg-type]
        response=response,
    )
    assert pending, "dispatch did not schedule run_cdp_worker"
    await pending[0]

    bound_contract = observed["bound_contract"]
    forwarded = expanded.get("contract", "NOT_FORWARDED")
    assert bound_contract is None and forwarded is None, (
        "absent job reached run_cdp_worker as "
        f"contract={bound_contract!r} "
        f"(maybe_expand_cdp_prompt contract={forwarded!r}; "
        f"call_site_kwarg_present={observed.get('call_had_contract_kwarg')!r})"
    )
