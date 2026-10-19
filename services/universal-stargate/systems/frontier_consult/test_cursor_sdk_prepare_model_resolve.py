"""Prepare path: default omitted model and cursor/* validation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import cursor_capabilities.cursor_capabilities as cap_mod
import pytest
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import load_auto_judgment_default_model

from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.admission import FrontierEndpointError


def _patch_prepare_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.densify_triage.validate_generate_density_intake",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.admission.enforce_check_review_substrate_admission",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_lane_ac_observer.prepare_lb_auto_review_for_generate",
        lambda **_k: (False, False, ""),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_lane_ac_observer.stamp_lb_review_spawn_fields",
        lambda **_k: ("source", None, False),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_lane_ac_observer.validate_generate_contract_packet_rules",
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
    monkeypatch.setattr(prepare_mod, "emit_sdk_generate_requested", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "emit_sdk_thread_created", lambda **_k: None)
    monkeypatch.setattr(
        prepare_mod, "create_handoff_thread", AsyncMock(return_value="thread-worker")
    )
    monkeypatch.setattr(
        prepare_mod, "admit_handoff_dispatch", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", AsyncMock())
    monkeypatch.setattr(
        "systems.frontier_consult.generate_admission_context_store.write_admission_context",
        lambda **_k: None,
    )


@pytest.mark.asyncio
async def test_prepare_omitted_model_uses_auto_judgment_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_prepare_deps(monkeypatch)
    expected = load_auto_judgment_default_model(load_route_policy())
    handle = await prepare_mod.prepare_cursor_sdk_generate(
        request_id="req-omit-model",
        role="cursor-sdk",
        model=None,
        subject="s",
        caller_agent="dispatch",
        contract="freeform",  # type: ignore[arg-type]
        packet_path=None,
        message_text="inline prompt body",
    )
    assert handle.resolved_model == expected
    assert expected.startswith("cursor/")


@pytest.mark.asyncio
async def test_prepare_denied_cursor_model_raises_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_prepare_deps(monkeypatch)
    monkeypatch.setattr(
        cap_mod,
        "CURSOR_DENIED_MODELS",
        frozenset({"claude-sonnet-5"}),
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        await prepare_mod.prepare_cursor_sdk_generate(
            request_id="req-denied-model",
            role="cursor-sdk",
            model="cursor/claude-sonnet-5",
            subject="s",
            caller_agent="dispatch",
            contract="freeform",  # type: ignore[arg-type]
            packet_path=None,
            message_text="inline prompt body",
        )
    assert exc_info.value.code == "sdk_generate_model_invalid"
