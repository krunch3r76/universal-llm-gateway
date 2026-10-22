"""Hermetic tests for impresario-ear-v1 handlers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from bus_watch.impresario_ear_common import (
    clear_hop_watermarks_for_tests,
    hop_watermark_key,
    hop_watermark_present,
    maybe_enqueue_after_hop,
    set_watermark_path_for_tests,
    try_claim_hop_watermark,
)
from implement_admission.impresario_policy import load_impresario_policy

from pipelines.impresario_ear.v1.handlers.deliver import deliver_impresario_ear
from pipelines.impresario_ear.v1.handlers.evaluate import (
    _build_evaluate_prompt,
    _extract_harvest_prose,
    evaluate_impresario_ear,
)
from pipelines.impresario_ear.v1.handlers.gather import gather_impresario_ear_context
from pipelines.impresario_ear.v1.handlers.resolve import resolve_impresario_ear

pytestmark = pytest.mark.offline

_ROOT = "18374"
_YAML = Path(__file__).resolve().parent / "impresario-ear-v1.yaml"


def test_impresario_ear_yaml_step_order() -> None:
    doc = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    names = [step["name"] for step in doc["steps"]]
    assert names == ["resolve", "gather", "evaluate", "deliver"]


def test_resolve_rejects_unknown_mode() -> None:
    out = resolve_impresario_ear(
        options={"mode": "stream", "root_thread": _ROOT},
        dispatch_thread_id=_ROOT,
    )
    assert out.get("skip") is True
    assert out["error"]["code"] == "ear.mode_invalid"


def test_resolve_dispatch_thread_missing() -> None:
    out = resolve_impresario_ear(options={"root_thread": _ROOT}, dispatch_thread_id=None)
    assert out["error"]["code"] == "ear.dispatch_thread_missing"


def test_resolve_ear_echo_guard_skips_when_tip_is_ear() -> None:
    def _fetch(**_: Any) -> dict[str, Any]:
        return {
            "turns": [
                {"from_agent": "impresario-ear", "subject": "EAR | 18374 — healthy"},
            ]
        }

    out = resolve_impresario_ear(
        options={"root_thread": _ROOT, "mode": "deep"},
        dispatch_thread_id=_ROOT,
        fetch_turns=_fetch,
    )
    assert out.get("skip") is True
    assert out.get("reason") == "ear_echo_guard"


def test_ear_echo_skips_when_newest_turn_is_ear_not_older_ear_in_window() -> None:
    """GET /turns is newest-first: tip EAR with older peer activity must skip."""

    def _fetch(**_: Any) -> dict[str, Any]:
        return {
            "turns": [
                {"from_agent": "impresario-ear", "subject": "EAR | 18374 — healthy"},
                {"from_agent": "cursor", "body": "operator steer after ear"},
            ]
        }

    out = resolve_impresario_ear(
        options={"root_thread": _ROOT, "mode": "deep"},
        dispatch_thread_id=_ROOT,
        fetch_turns=_fetch,
    )
    assert out.get("skip") is True
    assert out.get("reason") == "ear_echo_guard"


def test_ear_echo_does_not_skip_when_newer_non_ear_follows_older_ear() -> None:
    def _fetch(**_: Any) -> dict[str, Any]:
        return {
            "turns": [
                {"from_agent": "cursor", "body": "fresh work after ear posted"},
                {"from_agent": "impresario-ear", "subject": "EAR | 18374 — healthy"},
            ]
        }

    out = resolve_impresario_ear(
        options={"root_thread": _ROOT, "mode": "deep"},
        dispatch_thread_id=_ROOT,
        fetch_turns=_fetch,
    )
    assert out.get("skip") is False


def test_ear_echo_does_not_skip_when_live_from_cursor_before_older_ear() -> None:
    def _fetch(**_: Any) -> dict[str, Any]:
        return {
            "turns": [
                {"from": "cursor", "subject": "status", "body": "working"},
                {"from_agent": "impresario-ear", "subject": "EAR | 18374 — healthy"},
            ]
        }

    out = resolve_impresario_ear(
        options={"root_thread": _ROOT, "mode": "deep"},
        dispatch_thread_id=_ROOT,
        fetch_turns=_fetch,
    )
    assert out.get("skip") is False


def test_hop_ear_echo_skip_does_not_consume_watermark(tmp_path: Path) -> None:
    set_watermark_path_for_tests(tmp_path / "wm.json")
    clear_hop_watermarks_for_tests()
    key = hop_watermark_key(_ROOT, departing_transcript_id="tx-echo")

    def _fetch(**_: Any) -> dict[str, Any]:
        return {
            "turns": [
                {"from_agent": "impresario-ear", "subject": "EAR | 18374 — healthy"},
            ]
        }

    out = resolve_impresario_ear(
        options={
            "root_thread": _ROOT,
            "trigger": "hop",
            "departing_transcript_id": "tx-echo",
        },
        dispatch_thread_id=_ROOT,
        fetch_turns=_fetch,
    )
    assert out.get("reason") == "ear_echo_guard"
    assert hop_watermark_present(key) is False
    assert try_claim_hop_watermark(key) is True


def test_hop_enqueue_cursor_sets_to_agent() -> None:
    with patch(
        "bus_watch.impresario_ear_common.enqueue_impresario_ear",
    ) as enqueue:
        maybe_enqueue_after_hop(
            root_thread=_ROOT,
            departing_transcript_id="dep-tx",
            channel="hop",
            to_agent="cursor",
        )
    opts = enqueue.call_args.args[0]
    assert opts["to_agent"] == "cursor"


def test_extract_harvest_prose_strips_sidecar_keeps_verdict() -> None:
    raw = (
        "---\nstatus: complete\nresidue: none\n---\n"
        "VERDICT: healthy\nDIAGNOSIS: ok\nSTEER: continue\n\n"
        "## effects_manifest\n{\"big\": \"json\"}"
    )
    assert _extract_harvest_prose(raw) == (
        "VERDICT: healthy\nDIAGNOSIS: ok\nSTEER: continue"
    )
    plain = "VERDICT: healthy\nSTEER: continue"
    assert _extract_harvest_prose(plain) == plain


@pytest.mark.asyncio
async def test_evaluate_prompt_includes_verdict_instructions() -> None:
    generate = AsyncMock(
        return_value={"dispatch_id": "d1", "thread_id": _ROOT},
    )
    poll = AsyncMock(return_value={"terminal": True, "status": "completed"})
    harvest = AsyncMock(return_value="VERDICT: healthy\nSTEER: continue")
    resolve = {
        "skip": False,
        "root_thread": _ROOT,
        "mode": "deep",
        "trigger": "on_demand",
    }
    await evaluate_impresario_ear(
        resolve=resolve,
        gather={"jsonl": {}},
        options={},
        sdk_generate=generate,
        sdk_poll=poll,
        sdk_harvest=harvest,
    )
    msg = generate.await_args.kwargs["message_text"]
    assert "VERDICT: healthy | remiss_duty | drift | blocked" in msg
    assert "DIAGNOSIS:" in msg
    assert "STEER:" in msg
    assert "NEXT_ADMIT:" in msg
    assert "passive confirmation" in msg.lower() or "confirming, describing" in msg


@pytest.mark.asyncio
async def test_evaluate_patrol_forwards_prescreen_signals_to_prompt() -> None:
    pre = {
        "skip_model": False,
        "signals": ["passive_issue_confirmation_parked"],
        "imperative": "NEXT_ADMIT: liaison-tick.py --set",
    }
    generate = AsyncMock(return_value={"dispatch_id": "d1", "thread_id": _ROOT})
    poll = AsyncMock(return_value={"terminal": True, "status": "completed"})
    harvest = AsyncMock(return_value="VERDICT: remiss_duty")
    await evaluate_impresario_ear(
        resolve={
            "skip": False,
            "root_thread": _ROOT,
            "mode": "patrol",
            "trigger": "patrol",
        },
        gather={"jsonl": {}},
        options={"patrol_prescreen_result": pre},
        sdk_generate=generate,
        sdk_poll=poll,
        sdk_harvest=harvest,
    )
    msg = generate.await_args.kwargs["message_text"]
    assert "passive_issue_confirmation_parked" in msg
    assert pre["imperative"] in msg


def test_prompts_yaml_and_build_evaluate_prompt_passive_remiss_parity() -> None:
    from pathlib import Path

    import yaml

    from pipelines.impresario_ear.v1.handlers.evaluate import _build_evaluate_prompt

    prompts_path = Path(__file__).resolve().parent / "prompts.yaml"
    prompts = yaml.safe_load(prompts_path.read_text(encoding="utf-8"))
    evaluate_prompt = prompts["prompts"]["impresario_ear.v1.evaluate"]
    blob = (
        evaluate_prompt["system_prompt"] + evaluate_prompt["template"]
    ).lower()
    assert "remiss_duty" in blob
    assert "stay" in blob
    built = _build_evaluate_prompt(
        root_thread=_ROOT,
        mode="patrol",
        trigger="patrol",
        gather={"jsonl": {}},
    ).lower()
    assert "remiss_duty" in built
    assert "confirming, describing" in built


@pytest.mark.asyncio
async def test_evaluate_ephemeral_sdk_harvest_does_not_post() -> None:
    generate = AsyncMock(
        return_value={"dispatch_id": "d1", "thread_id": _ROOT},
    )
    poll = AsyncMock(return_value={"terminal": True, "status": "completed"})
    harvest = AsyncMock(return_value="VERDICT: healthy\nSTEER: continue")
    resolve = {
        "skip": False,
        "root_thread": _ROOT,
        "mode": "deep",
        "trigger": "on_demand",
    }
    gather = {"jsonl": {}}
    out = await evaluate_impresario_ear(
        resolve=resolve,
        gather=gather,
        options={},
        sdk_generate=generate,
        sdk_poll=poll,
        sdk_harvest=harvest,
    )
    assert out.get("skip") is False
    assert generate.await_args.kwargs["bus_lifecycle"] == "ephemeral"
    assert generate.await_args.kwargs["dispatch_thread_id"] is None
    assert "agent_bus" in generate.await_args.kwargs["message_text"]


@pytest.mark.asyncio
async def test_evaluate_patrol_pick_critic_does_not_typeerror() -> None:
    load_impresario_policy.cache_clear()
    generate = AsyncMock(
        return_value={"dispatch_id": "d1", "thread_id": _ROOT},
    )
    poll = AsyncMock(return_value={"terminal": True, "status": "completed"})
    harvest = AsyncMock(return_value="VERDICT: healthy")
    resolve = {"skip": False, "root_thread": _ROOT, "mode": "patrol", "trigger": "patrol"}
    gather = {"jsonl": {"glass_status": "completed", "transcript_id": _ROOT}}
    out = await evaluate_impresario_ear(
        resolve=resolve,
        gather=gather,
        options={},
        patrol_prescreen=AsyncMock(return_value={"skip_model": False}),
        sdk_generate=generate,
        sdk_poll=poll,
        sdk_harvest=harvest,
    )
    assert out.get("skip") is False
    assert "model" in out


@pytest.mark.asyncio
async def test_evaluate_deep_uses_argon_effort_high() -> None:
    load_impresario_policy.cache_clear()
    generate = AsyncMock(
        return_value={"dispatch_id": "d1", "thread_id": _ROOT},
    )
    poll = AsyncMock(return_value={"terminal": True, "status": "completed"})
    harvest = AsyncMock(return_value="VERDICT: healthy")
    resolve = {"skip": False, "root_thread": _ROOT, "mode": "deep", "trigger": "hop"}
    out = await evaluate_impresario_ear(
        resolve=resolve,
        gather={},
        options={},
        sdk_generate=generate,
        sdk_poll=poll,
        sdk_harvest=harvest,
    )
    assert out["model"] == "cursor/gemini-4-argon"
    assert out["effort"] == "high"
    assert generate.await_args.kwargs["model_knobs"]["effort"] == "high"


@pytest.mark.asyncio
async def test_deliver_posts_ear_from_impresario_ear() -> None:
    sent: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        sent.update(kwargs)
        return {"turn_number": 9}, 200

    out = await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": False},
        evaluate={"skip": False, "verdict": "healthy", "body": "STEER: go"},
        bus_send=_send,
    )
    assert out["posted"] is True
    assert sent["from_agent"] == "impresario-ear"
    assert sent["to_agent"] == "cursor"
    assert sent["from_agent"] != sent["to_agent"]
    assert sent["subject"].startswith("EAR |")


@pytest.mark.asyncio
async def test_deliver_posts_healthy_unless_dry_run() -> None:
    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        return {"turn_number": 1}, 200

    out = await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": False, "trigger": "hop"},
        evaluate={"skip": False, "verdict": "healthy", "body": "ok"},
        bus_send=_send,
    )
    assert out.get("posted") is True


@pytest.mark.asyncio
async def test_deliver_dry_run_returns_body_without_post() -> None:
    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        raise AssertionError("bus_send must not run")

    out = await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": True},
        evaluate={"skip": False, "verdict": "healthy", "body": "payload"},
        bus_send=_send,
    )
    assert out["dry_run"] is True
    assert out["posted"] is False
    assert out["body"] == "payload"


def test_hop_watermark_second_enqueue_is_noop(tmp_path: Path) -> None:
    set_watermark_path_for_tests(tmp_path / "wm.json")
    clear_hop_watermarks_for_tests()
    key = hop_watermark_key(_ROOT, departing_transcript_id="tx-1")
    assert try_claim_hop_watermark(key) is True
    out = resolve_impresario_ear(
        options={
            "root_thread": _ROOT,
            "trigger": "hop",
            "departing_transcript_id": "tx-1",
        },
        dispatch_thread_id=_ROOT,
    )
    assert out.get("reason") == "hop_watermark_present"
    assert hop_watermark_present(key)


def test_finalize_hop_enqueues_when_watermark_missing() -> None:
    from bus_watch.ide_hop import finalize_xvfb_hop_sidebar

    clear_hop_watermarks_for_tests()
    with patch(
        "bus_watch.cursor_xvfb_subscription.subscribe_cursor_xvfb_house",
        return_value={"ok": True},
    ), patch(
        "bus_watch.impresario_ear_common.maybe_enqueue_after_hop", return_value=True
    ) as enqueue:
        out = finalize_xvfb_hop_sidebar(
            _ROOT,
            departing_agent_id="depart-tx",
            landed_agent_id="land-tx",
        )
    assert out.get("impresario_ear") is True
    assert enqueue.called


def test_finalize_hop_skips_when_watermark_present(tmp_path: Path) -> None:
    from bus_watch.ide_hop import finalize_xvfb_hop_sidebar

    set_watermark_path_for_tests(tmp_path / "wm.json")
    clear_hop_watermarks_for_tests()
    try_claim_hop_watermark(hop_watermark_key(_ROOT, departing_transcript_id="depart-tx"))
    with patch(
        "bus_watch.cursor_xvfb_subscription.subscribe_cursor_xvfb_house",
        return_value={"ok": True},
    ), patch(
        "bus_watch.impresario_ear_common.maybe_enqueue_after_hop"
    ) as enqueue:
        out = finalize_xvfb_hop_sidebar(
            _ROOT,
            departing_agent_id="depart-tx",
            landed_agent_id="land-tx",
        )
    assert out.get("impresario_ear") is False
    enqueue.assert_not_called()


def test_grok_hop_enqueues_ear_without_ide_hop() -> None:
    from pipelines.grokbot_checkpoint.v1.handlers import post as grok_post

    with patch("bus_watch.impresario_ear_common.maybe_enqueue_after_hop", return_value=True) as hop:
        import asyncio

        asyncio.run(
            grok_post.run_post(
                {
                    "root_thread": _ROOT,
                    "house": "18374",
                    "n": 1,
                    "channel": "hop",
                    "mode": "seal",
                    "agent_id": "grok-tab-1",
                    "bus_identity": "grok-bot",
                    "verifier": "grok-bot",
                },
                {"session_id": "sess", "transcript_id": "grok-tab-1"},
                {"snapshot_uri": "snap", "turn_count": 1, "messages_sha256": "abc"},
                get=AsyncMock(
                    return_value=({"turns": []}, 200),
                ),
                send=AsyncMock(return_value=({"turn_number": 5}, 200)),
            )
        )
    hop.assert_called_once()


@pytest.mark.asyncio
async def test_gather_measure_ide_tab_typeerror_propagates() -> None:
    with patch(
        "bus_watch.ide_budget.measure_ide_tab",
        MagicMock(side_effect=TypeError("unexpected keyword 'register'")),
    ):
        with pytest.raises(TypeError, match="unexpected keyword 'register'"):
            await gather_impresario_ear_context(
                root_thread="18677",
                tape_fetch=lambda *a, **k: {"cells": []},
            )


@pytest.mark.asyncio
async def test_gather_measure_ide_tab_uses_lock_and_policy_not_register() -> None:
    lock = object()
    policy = {"gui_host": ":8", "successor_model": "cursor/gemini-4-argon"}
    measure = MagicMock(return_value={"transcript_id": "tx-18677"})
    with patch(
        "bus_watch.ide_budget.measure_ide_tab",
        measure,
    ), patch(
        "bus_watch.tick_state.load_state",
        return_value={"register": "attended"},
    ), patch(
        "bus_watch.digest_budget.effective_policy",
        return_value=policy,
    ), patch(
        "bus_watch.fable_lock.read_lock",
        return_value=lock,
    ), patch(
        "bus_watch.ide_budget.probe_glass_composer_budget",
        return_value={"status": "idle"},
    ):
        await gather_impresario_ear_context(
            root_thread="18677",
            tape_fetch=lambda *_a, **_k: {"cells": []},
        )
    measure.assert_called_once_with("18677", lock, policy)
    assert "register" not in (measure.call_args.kwargs or {})


@pytest.mark.asyncio
async def test_gather_default_cdp_probe_uses_transcript_id_and_gui_host() -> None:
    policy = {"gui_host": ":9"}
    probe = MagicMock(return_value={"pct": 42})
    with patch(
        "bus_watch.ide_budget.measure_ide_tab",
        MagicMock(return_value={"transcript_id": "trans-abc"}),
    ), patch(
        "bus_watch.tick_state.load_state",
        return_value={},
    ), patch(
        "bus_watch.digest_budget.effective_policy",
        return_value=policy,
    ), patch(
        "bus_watch.fable_lock.read_lock",
        return_value={},
    ), patch(
        "bus_watch.ide_budget.probe_glass_composer_budget",
        probe,
    ):
        await gather_impresario_ear_context(
            root_thread="18677",
            tape_fetch=lambda *_a, **_k: {"cells": []},
        )
    probe.assert_called_once_with("trans-abc", ":9")


def test_evaluate_gather_slice_starts_with_jsonl_when_tape_long() -> None:
    long_tape = "T" * 20000
    gather = {
        "tape": long_tape,
        "continuity": {"checkpoint_present": True},
        "jsonl": {"transcript_id": "tx-priority", "glass_status": "completed"},
        "cdp": {"attempted": True, "ok": True},
    }
    prompt = _build_evaluate_prompt(
        root_thread=_ROOT,
        mode="deep",
        trigger="hop",
        gather=gather,
    )
    start = prompt.index("<untrusted_context>\n") + len("<untrusted_context>\n")
    end = prompt.index("\n</untrusted_context>", start)
    gather_json = prompt[start:end]
    assert len(gather_json) == 12000
    assert gather_json.index('"jsonl"') < gather_json.index('"tape"')


@pytest.mark.asyncio
async def test_gather_completes_when_cdp_times_out() -> None:
    async def _boom() -> dict[str, Any]:
        raise TimeoutError("cdp")

    out = await gather_impresario_ear_context(
        root_thread=_ROOT,
        cdp_probe=_boom,
        tape_fetch=lambda *_a, **_k: {"cells": []},
    )
    assert out["cdp"]["attempted"] is True
    assert out["cdp"]["ok"] is False
    assert out["tape"]


@pytest.mark.asyncio
async def test_gather_includes_cdp_enrichment_when_reachable() -> None:
    async def _ok() -> dict[str, Any]:
        return {"status": "generating"}

    out = await gather_impresario_ear_context(
        root_thread=_ROOT,
        cdp_probe=_ok,
        tape_fetch=lambda *_a, **_k: {"cells": []},
    )
    assert out["cdp"]["ok"] is True
    assert out["cdp"]["payload"]["status"] == "generating"


@pytest.mark.asyncio
async def test_evaluate_degraded_provider_error_does_not_verdict_remiss() -> None:
    """16416#2118 shape: degraded SDK closeout must not fall through to remiss_duty."""
    harvest = (
        "status: degraded\n"
        "reason: provider_error: Provider Error We're having trouble connecting "
        "to the model provider.\n"
        "Later narrative mentions remiss duty without VERDICT line."
    )
    generate = AsyncMock(return_value={"dispatch_id": "d1", "thread_id": _ROOT})
    poll = AsyncMock(return_value={"terminal": True, "status": "completed"})
    harvest_fn = AsyncMock(return_value=harvest)
    out = await evaluate_impresario_ear(
        resolve={"skip": False, "root_thread": _ROOT, "mode": "deep", "trigger": "hop"},
        gather={},
        options={},
        sdk_generate=generate,
        sdk_poll=poll,
        sdk_harvest=harvest_fn,
    )
    assert out.get("skip") is False
    assert out.get("degraded_notice") is True
    assert "verdict" not in out
    assert out["body"] == (
        "status: degraded\n"
        "reason: provider_error: Provider Error We're having trouble connecting "
        "to the model provider."
    )


@pytest.mark.asyncio
async def test_deliver_degraded_provider_error_posts_notice_subject() -> None:
    sent: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        sent.update(kwargs)
        return {"turn_number": 11}, 200

    body = (
        "status: degraded\n"
        "reason: provider_error: Provider Error We're having trouble connecting "
        "to the model provider."
    )
    out = await deliver_impresario_ear(
        resolve={"root_thread": "16416", "dry_run": False},
        evaluate={
            "skip": False,
            "degraded_notice": True,
            "degraded_reason_token": "provider_error",
            "body": body,
        },
        bus_send=_send,
    )
    assert out["posted"] is True
    assert sent["subject"] == "DEGRADED | 16416 — provider_error"
    assert sent["body"] == body
    assert sent["from_agent"] == "impresario-ear"


@pytest.mark.asyncio
async def test_deliver_degraded_zero_tool_calls_subject() -> None:
    sent: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        sent.update(kwargs)
        return {"turn_number": 2}, 200

    body = "status: degraded\nreason: zero_tool_calls"
    out = await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": False},
        evaluate={
            "skip": False,
            "degraded_notice": True,
            "degraded_reason_token": "zero_tool_calls",
            "body": body,
        },
        bus_send=_send,
    )
    assert out["posted"] is True
    assert sent["subject"] == f"DEGRADED | {_ROOT} — zero_tool_calls"
    assert sent["body"] == body


@pytest.mark.asyncio
async def test_evaluate_zero_tool_calls_degraded_notice() -> None:
    harvest = (
        "status: degraded\n"
        "reason: zero_tool_calls\n"
        "Unstructured spill that mentions remiss later."
    )
    generate = AsyncMock(return_value={"dispatch_id": "d1", "thread_id": _ROOT})
    out = await evaluate_impresario_ear(
        resolve={"skip": False, "root_thread": _ROOT, "mode": "deep", "trigger": "hop"},
        gather={},
        options={},
        sdk_generate=generate,
        sdk_poll=AsyncMock(return_value={"terminal": True, "status": "completed"}),
        sdk_harvest=AsyncMock(return_value=harvest),
    )
    assert out.get("degraded_notice") is True
    assert out["degraded_reason_token"] == "zero_tool_calls"
    assert "verdict" not in out


@pytest.mark.asyncio
async def test_evaluate_verdict_remiss_still_ear_subject() -> None:
    harvest = (
        "VERDICT: remiss_duty\n"
        "DIAGNOSIS: house stalled\n"
        "STEER: dispatch\n"
        "NEXT_ADMIT: team_dispatch foo"
    )
    generate = AsyncMock(return_value={"dispatch_id": "d1", "thread_id": _ROOT})
    out = await evaluate_impresario_ear(
        resolve={"skip": False, "root_thread": _ROOT, "mode": "deep", "trigger": "hop"},
        gather={},
        options={},
        sdk_generate=generate,
        sdk_poll=AsyncMock(return_value={"terminal": True, "status": "completed"}),
        sdk_harvest=AsyncMock(return_value=harvest),
    )
    assert out.get("degraded_notice") is not True
    assert out["verdict"] == "remiss_duty"

    sent: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        sent.update(kwargs)
        return {"turn_number": 3}, 200

    await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": False},
        evaluate=out,
        bus_send=_send,
    )
    assert sent["subject"] == f"EAR | {_ROOT} — remiss_duty"


@pytest.mark.asyncio
async def test_evaluate_healthy_with_provider_error_in_diagnosis_stays_ear() -> None:
    harvest = (
        "VERDICT: healthy\n"
        "DIAGNOSIS: prior provider_error noise in gather only\n"
        "STEER: continue\n"
        "NEXT_ADMIT: none"
    )
    out = await evaluate_impresario_ear(
        resolve={"skip": False, "root_thread": _ROOT, "mode": "deep", "trigger": "hop"},
        gather={},
        options={},
        sdk_generate=AsyncMock(return_value={"dispatch_id": "d1", "thread_id": _ROOT}),
        sdk_poll=AsyncMock(return_value={"terminal": True, "status": "completed"}),
        sdk_harvest=AsyncMock(return_value=harvest),
    )
    assert out.get("degraded_notice") is not True
    assert out["verdict"] == "healthy"

    sent: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        sent.update(kwargs)
        return {"turn_number": 4}, 200

    await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": False},
        evaluate=out,
        bus_send=_send,
    )
    assert sent["subject"] == f"EAR | {_ROOT} — healthy"


@pytest.mark.asyncio
async def test_evaluate_patrol_prescreen_remiss_delivers_ear() -> None:
    prescreen = AsyncMock(
        return_value={
            "skip_model": True,
            "signals": ["pending_dispatch"],
            "imperative": "NEXT_ADMIT: team_dispatch",
        }
    )
    out = await evaluate_impresario_ear(
        resolve={"skip": False, "root_thread": _ROOT, "mode": "patrol", "trigger": "patrol"},
        gather={"jsonl": {}},
        options={},
        patrol_prescreen=prescreen,
    )
    assert out["verdict"] == "remiss_duty"

    sent: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        sent.update(kwargs)
        return {"turn_number": 5}, 200

    await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": False},
        evaluate=out,
        bus_send=_send,
    )
    assert sent["subject"] == f"EAR | {_ROOT} — remiss_duty"


_PATROL_SPECIMEN = (
    Path(__file__).resolve().parents[3]
    / "libs"
    / "bus_watch"
    / "fixtures"
    / "patrol_specimen_18374.json"
)


@pytest.mark.asyncio
async def test_evaluate_patrol_uses_carried_prescreen_after_patrol_pass(
    tmp_path: Path,
) -> None:
    from bus_watch.impresario_ear_patrol import (
        clear_patrol_state_for_tests,
        run_patrol_pass,
        set_patrol_cooldown_path_for_tests,
    )

    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    payload = json.loads(_PATROL_SPECIMEN.read_text(encoding="utf-8"))
    store = MagicMock()
    store.subscriptions.return_value = [
        {
            "surface": "cursor",
            "thread_scope": _ROOT,
            "agent_id": "81fb6960-950d-59c5-88b4-af3839f170d1",
            "glass_status": "completed",
        }
    ]
    admitted: list[dict[str, Any]] = []

    def _admit(opts: dict[str, Any], **_: Any) -> None:
        admitted.append(opts)

    with patch(
        "bus_watch.impresario_ear_patrol.load_patrol_prescreen_inputs",
        return_value=payload,
    ):
        stats = run_patrol_pass(store, now=10_000.0, admit_pipeline=_admit)
    assert stats["enqueued"] >= 1
    pipeline_opts = admitted[0]
    carried = pipeline_opts.get("patrol_prescreen_result")
    assert isinstance(carried, dict)
    assert carried.get("signals")

    generate = AsyncMock(return_value={"dispatch_id": "d1", "thread_id": _ROOT})
    poll = AsyncMock(return_value={"terminal": True, "status": "completed"})
    harvest = AsyncMock(return_value="VERDICT: healthy")
    out = await evaluate_impresario_ear(
        resolve={
            "skip": False,
            "root_thread": _ROOT,
            "mode": "patrol",
            "trigger": "patrol",
        },
        gather={"jsonl": {"glass_status": "completed", "transcript_id": _ROOT}},
        options=pipeline_opts,
        sdk_generate=generate,
        sdk_poll=poll,
        sdk_harvest=harvest,
    )
    assert generate.await_count == 1
    assert out.get("model") != "zero-token"


@pytest.mark.asyncio
async def test_evaluate_patrol_repriscreens_when_no_carried_result(
    tmp_path: Path,
) -> None:
    from bus_watch.impresario_ear_patrol import (
        clear_patrol_state_for_tests,
        set_patrol_cooldown_path_for_tests,
    )

    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    prescreen = AsyncMock(
        return_value={
            "skip_model": True,
            "signals": ["pending_dispatch"],
            "imperative": "NEXT_ADMIT: team_dispatch",
        }
    )
    out = await evaluate_impresario_ear(
        resolve={
            "skip": False,
            "root_thread": _ROOT,
            "mode": "patrol",
            "trigger": "patrol",
        },
        gather={"jsonl": {}},
        options={},
        patrol_prescreen=prescreen,
    )
    prescreen.assert_awaited_once()
    assert out.get("model") == "zero-token"
    assert out.get("verdict") == "remiss_duty"


@pytest.mark.asyncio
async def test_evaluate_incomplete_poll_skips_deliver() -> None:
    out = await evaluate_impresario_ear(
        resolve={"skip": False, "root_thread": _ROOT, "mode": "deep", "trigger": "hop"},
        gather={},
        options={},
        sdk_generate=AsyncMock(return_value={"dispatch_id": "d1", "thread_id": _ROOT}),
        sdk_poll=AsyncMock(return_value={"terminal": False, "status": "running"}),
        sdk_harvest=AsyncMock(),
    )
    assert out.get("skip") is True
    assert out["error"]["code"] == "ear.sdk_incomplete"

    deliver_out = await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": False},
        evaluate=out,
        bus_send=AsyncMock(),
    )
    assert deliver_out.get("skip") is True


@pytest.mark.asyncio
async def test_deliver_degraded_dry_run_no_bus_send() -> None:
    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        raise AssertionError("bus_send must not run")

    body = "status: degraded\nreason: provider_error: down"
    out = await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": True},
        evaluate={
            "skip": False,
            "degraded_notice": True,
            "degraded_reason_token": "provider_error",
            "body": body,
        },
        bus_send=_send,
    )
    assert out["dry_run"] is True
    assert out["posted"] is False
    assert out["subject"] == f"DEGRADED | {_ROOT} — provider_error"


@pytest.mark.asyncio
async def test_deliver_degraded_bus_failure() -> None:
    async def _send(**kwargs: Any) -> tuple[dict[str, Any], int]:
        return {"error": "bad"}, 503

    out = await deliver_impresario_ear(
        resolve={"root_thread": _ROOT, "dry_run": False},
        evaluate={
            "skip": False,
            "degraded_notice": True,
            "degraded_reason_token": "provider_error",
            "body": "status: degraded\nreason: provider_error: x",
        },
        bus_send=_send,
    )
    assert out.get("skip") is True
    assert out["error"]["code"] == "ear.deliver_failed"
    assert out.get("posted") is not True


def test_continuity_ops_omit_ear() -> None:
    continuity_py = (
        Path(__file__).resolve().parents[3]
        / "services"
        / "mcp-server"
        / "tools"
        / "continuity.py"
    )
    text = continuity_py.read_text(encoding="utf-8")
    assert '"ear"' not in text
    assert "op=\"ear\"" not in text
