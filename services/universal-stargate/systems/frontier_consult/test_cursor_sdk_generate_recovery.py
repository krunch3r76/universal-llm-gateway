"""Prepared-handle identity stability for cursor-sdk generate recovery."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from systems.frontier_consult import cursor_sdk_generate as generate_mod
from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.cursor_sdk_generate_prepare import (
    PreparedCursorSdkHandle,
    handle_from_dict,
    handle_to_dict,
)


def _patch_prepare_deps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    created: list[str] = []

    async def _create(**_kwargs: object) -> str:
        created.append("new")
        return "thread-stable"

    monkeypatch.setattr(
        prepare_mod,
        "resolve_cursor_sdk_generate_target",
        lambda *_a, **_k: (
            "cursor-sdk:dispatch:exec-fixture",
            "cursor",
            "sdk",
            "composer-2.5",
        ),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.densify_triage.validate_generate_density_intake",
        lambda **_k: None,
    )
    monkeypatch.setattr(prepare_mod, "emit_sdk_generate_requested", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "emit_sdk_thread_created", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "create_handoff_thread", _create)
    monkeypatch.setattr(
        prepare_mod, "admit_handoff_dispatch", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", AsyncMock())
    monkeypatch.setattr(
        "systems.frontier_consult.light_bounded_ac_observer.prepare_lb_auto_review_for_generate",
        lambda **_k: (False, False, "packet body"),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.light_bounded_ac_observer.validate_generate_contract_packet_rules",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.admission.enforce_check_review_substrate_admission",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        prepare_mod,
        "align_cursor_knobs",
        lambda **_k: type(
            "A",
            (),
            {
                "aligned_knobs": None,
                "warnings_as_dicts": lambda self: [],
                "knob_resolution_as_dicts": lambda self: [],
            },
        )(),
    )
    return created


@pytest.mark.asyncio
async def test_prepared_handle_retry_reuses_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _patch_prepare_deps(monkeypatch)
    worker_calls: list[dict[str, object]] = []

    async def _worker(**kwargs: object) -> tuple[bool, dict]:
        worker_calls.append(dict(kwargs))
        return True, {
            "status_code": 200,
            "dispatch_id": kwargs["dispatch_id"],
            "ticket": {"dispatch_id": kwargs["dispatch_id"]},
        }

    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker", _worker)
    monkeypatch.setattr(generate_mod, "emit_sdk_worker_outcome", lambda **_k: None)

    handle = await prepare_mod.prepare_cursor_sdk_generate(
        request_id="req-stable",
        role="cursor-sdk",
        model=None,
        subject="s",
        caller_agent="dispatch",
        contract="light-bounded",
        packet_path="tmp/packet.md",
        message_text=None,
    )
    assert handle.thread_id == "thread-stable"
    assert len(created) == 1

    first = await generate_mod.dispatch_prepared_cursor_sdk(handle)
    second = await generate_mod.dispatch_prepared_cursor_sdk(handle)

    assert first["dispatch_id"] == handle.dispatch_id
    assert second["dispatch_id"] == handle.dispatch_id
    assert first["thread_id"] == handle.thread_id
    assert second["thread_id"] == handle.thread_id
    assert worker_calls[0]["dispatch_id"] == handle.dispatch_id
    assert worker_calls[1]["dispatch_id"] == handle.dispatch_id
    assert worker_calls[0]["execution_id"] == handle.execution_id
    assert worker_calls[1]["execution_id"] == handle.execution_id
    assert worker_calls[0]["thread_id"] == handle.thread_id
    assert len(created) == 1


@pytest.mark.asyncio
async def test_handle_roundtrip_preserves_fingerprint_fields() -> None:
    handle = PreparedCursorSdkHandle(
        request_id="r1",
        execution_id="e1",
        dispatch_id="r1-aaaaaaaa",
        thread_id="t1",
        resolved_model="composer-2.5",
        role="cursor-sdk",
        family="cursor",
        platform="sdk",
        to_agent="cursor-sdk:dispatch:e1",
        handoff_contract="light-bounded",
        packet_path="tmp/p.md",
        message=None,
        caller_agent="web-anthropic",
        read_only=False,
        aligned_knobs=None,
        prompt_preamble=None,
        thread_subject="subj",
        pointer_body="ptr",
        effective_bus_lifecycle="persistent",
        parent_dispatch_thread_id="4917",
        dispatch_thread_id=None,
        density_triage=None,
        review_opt_out_reason_code=None,
        auto_review_child=False,
        auto_review_defaulted=False,
        claimed_via_atomic=False,
        admitted=True,
        alignment_warnings=(),
        knob_resolution=(),
    )
    restored = handle_from_dict(handle_to_dict(handle))
    assert restored == handle


@pytest.mark.asyncio
async def test_generate_composes_prepare_then_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _patch_prepare_deps(monkeypatch)
    worker = AsyncMock(
        return_value=(True, {"status_code": 200, "dispatch_id": "x", "ticket": {}})
    )
    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker", worker)
    monkeypatch.setattr(generate_mod, "emit_sdk_worker_outcome", lambda **_k: None)

    result = await generate_mod.dispatch_cursor_sdk_generate(
        request_id="req-compose",
        role="cursor-sdk",
        model=None,
        subject="s",
        caller_agent="dispatch",
        contract="light-bounded",
        packet_path="tmp/packet.md",
        message_text=None,
    )
    assert result["thread_id"] == "thread-stable"
    assert "dispatch_id" in result
    assert len(created) == 1
    worker.assert_awaited_once()
    assert worker.await_args.kwargs["dispatch_id"] == result["dispatch_id"]
