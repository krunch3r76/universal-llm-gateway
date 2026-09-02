"""Prepare-level fence: unattended Other Models op=generate on seat=cursor-sdk."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from cursor_capabilities import is_other_models_pool

from systems.frontier_consult import cursor_sdk_generate as generate_mod
from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod
from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cursor_sdk_pool_fence import (
    reject_other_models_pool_generate,
)


def _patch_prepare_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    resolved_model: str,
) -> dict[str, object]:
    mint_calls: list[object] = []
    created: list[str] = []
    admitted: list[str] = []
    context_writes: list[str] = []
    published: list[object] = []

    monkeypatch.setattr(
        prepare_mod,
        "resolve_cursor_sdk_generate_target",
        lambda *_a, **_k: (
            "cursor-sdk:dispatch:exec-fixture",
            "cursor",
            "sdk",
            resolved_model,
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

    def _mint(**_kwargs: object) -> tuple[str, str]:
        mint_calls.append("mint")
        return "exec-minted", "disp-minted"

    monkeypatch.setattr(prepare_mod, "mint_cursor_sdk_ids", _mint)
    monkeypatch.setattr(
        prepare_mod,
        "create_handoff_thread",
        AsyncMock(return_value="t1"),
    )
    monkeypatch.setattr(
        prepare_mod, "admit_handoff_dispatch", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        prepare_mod,
        "claim_and_post_pointer_turn",
        AsyncMock(return_value=(True, 1)),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_admission_context_store.write_admission_context",
        lambda **_k: context_writes.append("context"),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_signals.publish_frontier_event",
        lambda event: published.append(event),
    )
    return {
        "mint_calls": mint_calls,
        "created": created,
        "admitted": admitted,
        "context_writes": context_writes,
        "published": published,
    }


@pytest.mark.parametrize(
    "model",
    [
        "cursor/claude-sonnet-5",
        "cursor/gpt-5.6-terra",
        "cursor/gpt-5.6-luna",
        "cursor/claude-fable-5",
    ],
)
def test_fence_explicit_other_models_pin_admitted_with_exempted_event(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    published: list[object] = []
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_signals.publish_frontier_event",
        lambda event: published.append(event),
    )
    reject_other_models_pool_generate(
        request_id="req-exempt",
        role="cursor-sdk",
        seat=None,
        model=model,
        resolved_model=model,
    )
    signals = [getattr(ev, "signal", "") for ev in published]
    assert signals == ["frontier.sdk.pool.exempted"]
    payload = published[0].payload
    assert payload["code"] == "other_models_pool_exempted"
    assert payload["requested_model"] == model
    assert payload["resolved_model"] == model
    assert payload["pool"] == "other_models"


@pytest.mark.parametrize("model", [None, "", "   "])
def test_fence_omit_path_other_models_refused(
    monkeypatch: pytest.MonkeyPatch, model: str | None
) -> None:
    published: list[object] = []
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_signals.publish_frontier_event",
        lambda event: published.append(event),
    )
    with pytest.raises(FrontierEndpointError) as excinfo:
        reject_other_models_pool_generate(
            request_id="req-omit",
            role="cursor-sdk",
            seat=None,
            model=model,
            resolved_model="cursor/claude-sonnet-5",
        )
    err = excinfo.value
    assert err.status_code == 422
    assert err.code == "other_models_pool_denied"
    assert err.field == "model"
    assert err.details["requested_model"] == model
    assert [getattr(ev, "signal", "") for ev in published] == [
        "frontier.sdk.pool.denied"
    ]


@pytest.mark.parametrize(
    "model,resolved",
    [
        ("cursor/grok-4.6", "cursor/grok-4.6"),
        ("cursor/composer-2.5", "cursor/composer-2.5"),
        (None, "cursor/composer-2.5"),
    ],
)
def test_prepare_cursor_models_host_do_not_raise_fence(
    model: str | None,
    resolved: str,
) -> None:
    assert not is_other_models_pool(resolved)
    reject_other_models_pool_generate(
        request_id="req-host",
        role="cursor-sdk",
        seat="cursor-sdk",
        model=model,
        resolved_model=resolved,
    )


@pytest.mark.parametrize(
    "model,resolved",
    [
        ("cursor/claude-sonnet-5", "cursor/claude-sonnet-5"),
        ("cursor/gpt-5.6-terra", "cursor/gpt-5.6-terra"),
        ("cursor/gpt-5.6-luna", "cursor/gpt-5.6-luna"),
        ("cursor/claude-fable-5", "cursor/claude-fable-5"),
    ],
)
@pytest.mark.asyncio
async def test_prepare_explicit_other_models_pin_reaches_mint(
    monkeypatch: pytest.MonkeyPatch, model: str, resolved: str
) -> None:
    probes = _patch_prepare_deps(monkeypatch, resolved_model=resolved)
    try:
        await prepare_mod.prepare_cursor_sdk_generate(
            request_id="req-pool",
            role="cursor-sdk",
            model=model,
            subject="s",
            caller_agent="dispatch",
            contract="light-bounded",
            packet_path="tmp/reviews/packet.md",
            message_text=None,
            execution_id=None,
            dispatch_id=None,
        )
    except FrontierEndpointError as exc:  # downstream fixture gaps are not this fence
        assert exc.code != "other_models_pool_denied"
    assert probes["mint_calls"] == ["mint"]
    signals = [getattr(ev, "signal", "") for ev in probes["published"]]
    assert "frontier.sdk.pool.exempted" in signals
    assert "frontier.sdk.pool.denied" not in signals


@pytest.mark.asyncio
async def test_prepare_omit_path_other_models_refused_before_mint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = _patch_prepare_deps(
        monkeypatch, resolved_model="cursor/claude-sonnet-5"
    )
    with pytest.raises(FrontierEndpointError) as excinfo:
        await prepare_mod.prepare_cursor_sdk_generate(
            request_id="req-omit",
            role="cursor-sdk",
            model=None,
            subject="s",
            caller_agent="dispatch",
            contract="light-bounded",
            packet_path="tmp/reviews/packet.md",
            message_text=None,
            execution_id=None,
            dispatch_id=None,
        )
    assert excinfo.value.code == "other_models_pool_denied"
    assert probes["mint_calls"] == []
    assert probes["context_writes"] == []
    assert [getattr(ev, "signal", "") for ev in probes["published"]] == [
        "frontier.sdk.pool.denied"
    ]


@pytest.mark.asyncio
async def test_e2e_generate_worker_not_called_for_omit_path_other_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = AsyncMock(return_value=(True, {"dispatch_id": "d1"}))
    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker", worker)
    monkeypatch.setattr(generate_mod, "dispatch_cursor_sdk_worker_message", worker)
    monkeypatch.setattr(
        prepare_mod,
        "resolve_cursor_sdk_generate_target",
        lambda *_a, **_k: (
            "cursor-sdk:dispatch:x",
            "cursor",
            "sdk",
            "cursor/claude-sonnet-5",
        ),
    )
    with pytest.raises(FrontierEndpointError) as excinfo:
        await generate_mod.dispatch_cursor_sdk_generate(
            request_id="req-e2e",
            role="cursor-sdk",
            model=None,
            subject="s",
            caller_agent="dispatch",
            contract="light-bounded",
            packet_path="tmp/reviews/packet.md",
            message_text=None,
        )
    assert excinfo.value.code == "other_models_pool_denied"
    worker.assert_not_awaited()
