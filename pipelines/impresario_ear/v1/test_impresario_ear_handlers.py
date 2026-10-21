"""Hermetic tests for impresario-ear-v1 handlers."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from bus_watch.impresario_ear_common import (
    clear_hop_watermarks_for_tests,
    hop_watermark_key,
    hop_watermark_present,
    set_watermark_path_for_tests,
    try_claim_hop_watermark,
)
from implement_admission.impresario_policy import load_impresario_policy
from pipelines.impresario_ear.v1.handlers.deliver import deliver_impresario_ear
from pipelines.impresario_ear.v1.handlers.evaluate import evaluate_impresario_ear
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
