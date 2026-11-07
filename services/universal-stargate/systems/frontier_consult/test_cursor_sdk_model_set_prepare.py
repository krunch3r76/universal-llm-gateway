"""Prepare-level model-set refusals (AC 6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.admission import FrontierEndpointError


def _patch_prepare_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    mint = MagicMock(side_effect=AssertionError("mint must not run"))
    monkeypatch.setattr(prepare_mod, "mint_cursor_sdk_ids", mint)
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
        "systems.frontier_consult.cursor_sdk_pool_fence.reject_other_models_pool_generate",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare_restart_busy.enforce_restart_busy_scoped_by_work_key",
        AsyncMock(),
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
async def test_prepare_refuses_bare_id_without_cursor_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_prepare_deps(monkeypatch)
    with pytest.raises(FrontierEndpointError) as exc_info:
        await prepare_mod.prepare_cursor_sdk_generate(
            request_id="req-ms-prepare-bare",
            role="cursor-sdk",
            model="gemini-4-argon",
            subject="s",
            caller_agent="dispatch",
            contract="freeform",  # type: ignore[arg-type]
            packet_path=None,
            message_text="inline",
        )
    err = exc_info.value
    assert err.code == "sdk_generate_model_invalid"
    assert err.status_code == 422


@pytest.mark.asyncio
async def test_prepare_explicit_other_models_argon_passes_model_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_prepare_deps(monkeypatch)
    fence = MagicMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_pool_fence.reject_other_models_pool_generate",
        fence,
    )
    monkeypatch.setattr(
        prepare_mod,
        "mint_cursor_sdk_ids",
        MagicMock(return_value=("exec-ms-argon", "disp-ms-argon")),
    )
    handle = await prepare_mod.prepare_cursor_sdk_generate(
        request_id="req-ms-prepare-argon",
        role="cursor-sdk",
        model="cursor/gemini-4-argon",
        subject="s",
        caller_agent="dispatch",
        contract="freeform",  # type: ignore[arg-type]
        packet_path=None,
        message_text="inline",
    )
    fence.assert_called_once()
    assert handle.resolved_model == "cursor/gemini-4-argon"
