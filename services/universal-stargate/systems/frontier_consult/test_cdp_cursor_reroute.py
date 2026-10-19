"""Offline tests for global CDP → cursor-sdk reroute."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from systems.frontier_consult.cdp_cursor_reroute import (
    load_cdp_reroute_config,
    resolve_cdp_reroute,
    run_cdp_reroute_via_cursor_sdk,
)

pytestmark = pytest.mark.offline

def _future_expires_at(*, days: int = 30) -> str:
    return (
        (datetime.now(UTC) + timedelta(days=days))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


_ENABLED_REROUTE_POLICY = {
    "enabled": True,
    "expires_at": _future_expires_at(),
    "target_model": "cursor/gemini-4-argon",
    "match_prefixes": ["cdp/opus", "cdp/fable"],
}


def _patch_cdp_reroute_route_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cdp_reroute: dict
) -> None:
    policy_path = tmp_path / "route_policy.yaml"
    policy_path.write_text(
        yaml.safe_dump({"cdp_reroute": cdp_reroute}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_cursor_reroute.load_route_policy",
        lambda path=None: yaml.safe_load(policy_path.read_text()),
    )


def test_resolve_cdp_reroute_matches_opus_and_fable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_cdp_reroute_route_policy(monkeypatch, tmp_path, _ENABLED_REROUTE_POLICY)
    cfg = load_cdp_reroute_config()
    assert cfg.get("enabled") is True
    assert resolve_cdp_reroute("cdp/opus-5.5-high") is not None
    assert resolve_cdp_reroute("cdp/fable-5.1") is not None
    assert resolve_cdp_reroute("cursor/grok-4.7") is None


def test_critic_impresario_role_blocks_reroute_when_flag_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_cdp_reroute_route_policy(monkeypatch, tmp_path, _ENABLED_REROUTE_POLICY)
    assert resolve_cdp_reroute("cdp/opus-5.5-high", impresario_role="skeptic") is None
    assert resolve_cdp_reroute("cdp/opus-5.5-high") is not None


def test_resolve_cdp_reroute_respects_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_cdp_reroute_route_policy(
        monkeypatch,
        tmp_path,
        {
            "enabled": False,
            "expires_at": _future_expires_at(),
            "target_model": "cursor/gemini-4-argon",
            "match_prefixes": ["cdp/opus"],
        },
    )
    assert resolve_cdp_reroute("cdp/opus-5") is None


def test_resolve_cdp_reroute_requires_future_expires_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks when enabled+prefix match without a future expires_at still reroutes."""
    base = {
        "enabled": True,
        "target_model": "cursor/gemini-4-argon",
        "match_prefixes": ["cdp/opus"],
    }
    _patch_cdp_reroute_route_policy(monkeypatch, tmp_path, base)
    assert resolve_cdp_reroute("cdp/opus-5") is None

    past = datetime(2020, 1, 1, tzinfo=UTC)
    _patch_cdp_reroute_route_policy(
        monkeypatch,
        tmp_path,
        {**base, "expires_at": "2019-12-31T23:59:59Z"},
    )
    assert resolve_cdp_reroute("cdp/opus-5", now=past) is None

    _patch_cdp_reroute_route_policy(
        monkeypatch,
        tmp_path,
        {**base, "expires_at": "not-an-instant"},
    )
    assert resolve_cdp_reroute("cdp/opus-5") is None

    _patch_cdp_reroute_route_policy(
        monkeypatch,
        tmp_path,
        {**base, "expires_at": _future_expires_at()},
    )
    assert resolve_cdp_reroute("cdp/opus-5") is not None


@pytest.mark.asyncio
async def test_run_cdp_reroute_harvests_and_archives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cortex = tmp_path / "cortex"
    cortex.mkdir()
    prompt_rel = "notes/system/prompts/exec-reroute/prompt.md"
    prompt_path = cortex / prompt_rel
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("Review this packet.", encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex))
    workspaces = tmp_path / "ws"
    workspaces.mkdir()
    ws_repo = workspaces / "universal-llm-gateway"
    ws_repo.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces))
    _patch_cdp_reroute_route_policy(monkeypatch, tmp_path, _ENABLED_REROUTE_POLICY)

    dispatch_id = "auto-reroute-test"
    sidecar = ws_repo / "tmp/reviews/closeouts" / f"{dispatch_id}.md"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("VERDICT: approve\n", encoding="utf-8")

    admit = {
        "dispatch_id": dispatch_id,
        "thread_id": "99001",
    }

    async def _fake_dispatch(**kwargs: object) -> dict[str, object]:
        assert kwargs.get("read_only") is True
        assert kwargs.get("lane") == "B"
        assert kwargs.get("model") == "cursor/gemini-4-argon"
        assert kwargs.get("model_knobs") == {"effort": "high"}
        assert kwargs.get("contract") == "freeform"
        return admit

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_cursor_reroute.dispatch_cursor_sdk_generate",
        _fake_dispatch,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_cursor_reroute._poll_dispatch_status",
        AsyncMock(
            return_value={"ok": True, "terminal": True, "status": "completed"}
        ),
    )

    execution_id = "abcd1234-5678-90ab-cdef-1234567890ab"
    result = await run_cdp_reroute_via_cursor_sdk(
        execution_id=execution_id,
        prompt_uri=f"cortex://{prompt_rel}",
        model_id="cdp/opus-5.5",
        thread_id="99001",
        caller_agent="test-agent",
        max_wall_s=30.0,
    )
    assert result.ok is True
    assert result.body == "VERDICT: approve"
    assert result.substrate == "cursor-sdk-reroute"
    assert result.archive_uri == "cortex://notes/system/threads/cdp-ask-archive-abcd1234.md"
    archive_path = cortex / "notes/system/threads/cdp-ask-archive-abcd1234.md"
    assert archive_path.is_file()
    assert archive_path.read_text(encoding="utf-8") == "VERDICT: approve"


@pytest.mark.asyncio
async def test_run_cdp_worker_uses_reroute_not_cdp_generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult import cdp_generate_worker as worker

    _patch_cdp_reroute_route_policy(monkeypatch, tmp_path, _ENABLED_REROUTE_POLICY)

    finalize = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.finalize_cdp_generate",
        finalize,
    )
    monkeypatch.setattr(worker, "publish_cdp_kwargs", lambda *a, **k: None)
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.maybe_expand_cdp_prompt",
        lambda **kw: kw["prompt_uri"],
    )

    reroute_result = CdpGenerateResult(
        ok=True,
        body="VERDICT: ship",
        execution_id="exec-reroute",
        satellite_execution_id="sdk-1",
        prompt_uri="cortex://notes/prompt.md",
        picker_model="gemini-4-argon",
        archive_uri="cortex://notes/system/threads/cdp-ask-archive-exec-rer.md",
        substrate="cursor-sdk-reroute",
    )
    reroute_mock = AsyncMock(return_value=reroute_result)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_cursor_reroute.run_cdp_reroute_via_cursor_sdk",
        reroute_mock,
    )
    generate_mock = MagicMock()
    monkeypatch.setattr(worker, "run_cdp_generate", generate_mock)

    await worker.run_cdp_worker(
        execution_id="exec-reroute",
        model_id="cdp/fable-5.1",
        thread_id="88001",
        caller_agent="dispatch",
        prompt_uri="cortex://notes/prompt.md",
        request_id="req-reroute",
    )

    reroute_mock.assert_awaited_once()
    generate_mock.assert_not_called()
    finalize.assert_awaited_once()
    assert finalize.await_args.kwargs["result"].substrate == "cursor-sdk-reroute"

