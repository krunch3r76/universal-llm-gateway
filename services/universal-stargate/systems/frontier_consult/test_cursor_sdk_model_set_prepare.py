"""Prepare-level model-set refusals (AC 6)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.admission import FrontierEndpointError


def _patch_prepare_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    mint = AsyncMock(side_effect=AssertionError("mint must not run"))
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    ["cursor/gemini-4-argon", "gemini-4-argon"],
)
async def test_prepare_refuses_non_member_before_mint(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    _patch_prepare_deps(monkeypatch)
    with pytest.raises(FrontierEndpointError) as exc_info:
        await prepare_mod.prepare_cursor_sdk_generate(
            request_id="req-ms-prepare",
            role="cursor-sdk",
            model=model,
            subject="s",
            caller_agent="dispatch",
            contract="freeform",  # type: ignore[arg-type]
            packet_path=None,
            message_text="inline",
        )
    err = exc_info.value
    assert err.code == "sdk_generate_model_invalid"
    assert err.status_code == 422
    assert err.details is not None
    assert "cursor/composer-2.5" in err.details["fix_hint"]
    assert "cursor/grok-4.7" in err.details["fix_hint"]
    assert err.details.get("retryable") is False
