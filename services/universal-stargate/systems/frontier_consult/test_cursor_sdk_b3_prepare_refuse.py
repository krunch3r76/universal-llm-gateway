"""B.3: refuse loop_closure at generate-prepare before handoff.created / coord post."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cursor_sdk_admit_loop import (
    reset_admit_pointer_would_have_refused_counter_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_counter() -> None:
    reset_admit_pointer_would_have_refused_counter_for_tests()


def _patch_prepare_deps(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    created: list[str] = []
    coord: list[tuple[object, ...]] = []
    requested: list[str] = []
    published: list[object] = []

    async def _create(**_kwargs: object) -> str:
        created.append("handoff")
        return "thread-worker"

    async def _coord(**kwargs: object) -> None:
        coord.append(tuple(kwargs.items()))

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
    monkeypatch.setattr(
        "systems.frontier_consult.admission.enforce_check_review_substrate_admission",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.light_bounded_ac_observer.prepare_lb_auto_review_for_generate",
        lambda **_k: (False, False, ""),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.light_bounded_ac_observer.stamp_lb_review_spawn_fields",
        lambda **_k: ("source", None, False),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.light_bounded_ac_observer.validate_generate_contract_packet_rules",
        lambda **_k: None,
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
    monkeypatch.setattr(
        prepare_mod,
        "emit_sdk_generate_requested",
        lambda **_k: requested.append("generate.requested"),
    )
    monkeypatch.setattr(prepare_mod, "emit_sdk_thread_created", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "create_handoff_thread", _create)
    monkeypatch.setattr(
        prepare_mod, "admit_handoff_dispatch", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", _coord)
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.publish_frontier_event",
        lambda event: published.append(event),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_admission_context_store.write_admission_context",
        lambda **_k: None,
    )
    return {
        "created": created,
        "coord": coord,
        "requested": requested,
        "published": published,
    }


@pytest.mark.asyncio
async def test_b3_refuse_before_handoff_and_coord(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = _patch_prepare_deps(monkeypatch)
    with pytest.raises(FrontierEndpointError) as excinfo:
        await prepare_mod.prepare_cursor_sdk_generate(
            request_id="req-b3",
            role="cursor-sdk",
            model=None,
            subject="s",
            caller_agent="dispatch",
            contract="light-bounded",
            packet_path=None,
            message_text="nest",
            parent_dispatch_thread_id="7435",
            dispatch_thread_id="7435",
            prompt_bind_mode="explicit_inline",
            prompt_turn_number=None,
        )
    err = excinfo.value
    assert err.status_code == 422
    assert err.code == "admit_pointer.loop_closure"
    assert err.details is not None
    assert err.details["retryable"] is False
    assert probes["created"] == []
    assert probes["coord"] == []
    assert probes["requested"] == []
    signals = [getattr(ev, "signal", "") for ev in probes["published"]]
    assert "frontier.admit_pointer.loop_closure" in signals
    payloads = [
        getattr(ev, "payload", {})
        for ev in probes["published"]
        if getattr(ev, "signal", "") == "frontier.admit_pointer.loop_closure"
    ]
    assert payloads and payloads[0].get("refused") is True
    assert "frontier.handoff.created" not in signals


@pytest.mark.asyncio
async def test_b3_row2_frozen_pin_still_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = _patch_prepare_deps(monkeypatch)
    handle = await prepare_mod.prepare_cursor_sdk_generate(
        request_id="req-row2",
        role="cursor-sdk",
        model=None,
        subject="s",
        caller_agent="dispatch",
        contract="light-bounded",
        packet_path=None,
        message_text="nest",
        parent_dispatch_thread_id="7435",
        dispatch_thread_id="7435",
        prompt_bind_mode="frozen_turn",
        prompt_turn_number=7,
    )
    assert handle.thread_id == "thread-worker"
    assert probes["created"] == ["handoff"]
    assert probes["requested"] == ["generate.requested"]
    signals = [getattr(ev, "signal", "") for ev in probes["published"]]
    assert "frontier.admit_pointer.loop_closure" not in signals


@pytest.mark.asyncio
async def test_b3_row1_cross_thread_still_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = _patch_prepare_deps(monkeypatch)
    handle = await prepare_mod.prepare_cursor_sdk_generate(
        request_id="req-row1",
        role="cursor-sdk",
        model=None,
        subject="s",
        caller_agent="dispatch",
        contract="light-bounded",
        packet_path=None,
        message_text="nest",
        parent_dispatch_thread_id="1959",
        dispatch_thread_id="1960",
        prompt_bind_mode="explicit_inline",
        prompt_turn_number=None,
    )
    assert handle.thread_id == "thread-worker"
    assert probes["created"] == ["handoff"]
