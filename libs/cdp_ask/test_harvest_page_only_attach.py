"""Acceptance proofs for the page-only harvest attach (B2a, B2b, B3)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from cdp_ask.cse_session_harvest import execute_harvest
from cdp_ask.cse_session_models import HarvestRequest
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_reattach import ensure_cse_attached
from cdp_ask.poll_recovery import recover_poll_snapshot
from cdp_ask.test_followup_reattach import (
    _FakeCtx,
    _FakePage,
    _connect_factory,
    _noop_launch,
    isolated_registry,
)
from claude_bundles.cdp_registry.models import _HOST_LISTABLE_STATUSES

U = "https://claude.ai/cowork/cse_pageonly_harvest"
OTHER_U = "https://claude.ai/cowork/cse_other_bound"
EXE = "68a8129ca2264fe088ec267a20d88376"


def _active_snapshot() -> dict[str, Any]:
    from claude_bundles import cdp_registry as reg

    return copy.deepcopy(reg._store.load_active())


def _host_listable_count(active: dict[str, Any]) -> int:
    return sum(
        1
        for row in active.values()
        if isinstance(row, dict) and row.get("status") in _HOST_LISTABLE_STATUSES
    )


def _registry_events(root: Path) -> list[dict[str, Any]]:
    path = root / "registry.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _forbidden_registry_events(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> bool:
    new = after[len(before) :]
    for ev in new:
        et = str(ev.get("event") or ev.get("type") or "")
        if et in {"session_address_bound", "relaunch"}:
            return True
    return False


def _assert_observables(
    before_active: dict[str, Any],
    after_active: dict[str, Any],
  *,
    chat_url: str,
    before_events: list[dict[str, Any]],
    after_events: list[dict[str, Any]],
    minted_released: int | None = None,
) -> None:
    for rid, row in before_active.items():
        assert after_active.get(rid) == row
    for rid, row in after_active.items():
        if not isinstance(row, dict):
            continue
        url = (row.get("chat_url") or "").strip()
        if url == chat_url:
            before_row = before_active.get(rid)
            assert isinstance(before_row, dict)
            assert (before_row.get("chat_url") or "").strip() == chat_url
    assert _host_listable_count(after_active) == _host_listable_count(before_active)
    assert not _forbidden_registry_events(before_events, after_events)
    if minted_released is not None:
        released = [
            row
            for row in after_active.values()
            if isinstance(row, dict) and row.get("status") == "released"
        ]
        new_released = len(released) - sum(
            1
            for row in before_active.values()
            if isinstance(row, dict) and row.get("status") == "released"
        )
        assert new_released == minted_released


def _patch_holder_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.git_integration_worker.cse_session_holders import RemoteUpsert

    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.upsert_holder_remote",
        lambda **_kwargs: RemoteUpsert.OK,
    )
    monkeypatch.setattr("claude_bundles.cdp_lane._launch_chrome", _noop_launch)


def _patch_harvest_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, metadata_scrape: bool
) -> list[str]:
    connect_calls: list[str] = []
    base = _connect_factory()

    async def _recording_connect(url: str) -> Any:
        connect_calls.append(url)
        return await base(url)

    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _recording_connect,
    )
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr("cdp_ask.cse_session_harvest.emit", lambda _e: None)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _patch_holder_remote(monkeypatch)
    if metadata_scrape:
        monkeypatch.setattr(
            "cdp_ask.cse_session_harvest_scrape.harvest_turns",
            AsyncMock(
                return_value={
                    "turns": [
                        {
                            "author": "assistant",
                            "text": "Harvested reply body long enough for scrape.",
                            "ordinal": 1,
                        }
                    ],
                    "streaming": False,
                    "stop": False,
                    "tool_pause": False,
                    "title": "Session",
                    "loading": False,
                }
            ),
        )
    return connect_calls


def _pages_with_url(ctxs: list[_FakeCtx], url: str) -> list[_FakePage]:
    out: list[_FakePage] = []
    for ctx in ctxs:
        for page in ctx.pages:
            if page.url == url:
                out.append(page)
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id",
    ["free_host_metadata_only", "free_host_scrape", "dormant_seat", "no_host_mint"],
    ids=["free_host_metadata_only", "free_host_scrape", "dormant_seat", "no_host_mint"],
)
async def test_harvest_changes_no_registration_row(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case_id: str,
) -> None:
    """A cse_session harvest leaves every row as it was and no new host listable."""
    from claude_bundles import cdp_registry as reg

    minted_released: int | None = None
    metadata_only = case_id != "free_host_scrape"
    metadata_scrape = case_id == "free_host_scrape"

    if case_id in {"free_host_metadata_only", "free_host_scrape"}:
        reg.register_lane(
            holder="free-host",
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
    elif case_id == "dormant_seat":
        reg.register_lane(
            holder="free-host",
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
        parked = reg.register_lane(
            holder="holder-parked",
            purpose="operator-proxy",
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
        assert reg.bind_session_address(parked.registration_id, chat_url=U)
        reg._release_driver_lock(parked.registration_id)
        assert (
            reg.make_dormant(parked.registration_id, is_listening=lambda _port: False)
            is not None
        )
    else:
        host = reg.register_lane(
            holder="occupied-host",
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
        assert reg.bind_session_address(host.registration_id, chat_url=OTHER_U)
        minted_released = 1
        metadata_scrape = True

    before_active = _active_snapshot()
    before_events = _registry_events(isolated_registry)
    _patch_harvest_harness(monkeypatch, tmp_path, metadata_scrape=metadata_scrape)
    store = ExecutionStore()
    req_kwargs: dict[str, Any] = {"chat_url": U, "metadata_only": metadata_only}
    if case_id == "free_host_scrape":
        req_kwargs["source"] = "chat"
    result = await execute_harvest(HarvestRequest(**req_kwargs), store)
    after_active = _active_snapshot()
    after_events = _registry_events(isolated_registry)
    _assert_observables(
        before_active,
        after_active,
        chat_url=U,
        before_events=before_events,
        after_events=after_events,
        minted_released=minted_released,
    )
    assert result.provenance is not None
    assert result.provenance.get("opened_on_demand") is True
    assert result.outcome not in {"not_attached", "unreachable"}


def _poll_recovery_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.snapshot_from_archive_token",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("cdp_ask.poll_recovery.chat_url_from_archives", lambda _t: None)
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.chat_url_from_provenance", lambda _t: None
    )
    monkeypatch.setattr("cdp_ask.poll_recovery._recovery_last_attempt", {})


@pytest.mark.asyncio
async def test_poll_recovery_changes_no_registration_row(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A poll-recovery harvest binds no row on the host it borrows."""
    from claude_bundles import cdp_registry as reg

    reg.register_lane(
        holder="free-host",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    before_active = _active_snapshot()
    before_events = _registry_events(isolated_registry)
    connect_calls = _patch_harvest_harness(monkeypatch, tmp_path, metadata_scrape=True)
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.current_model_label",
        AsyncMock(return_value="claude-sonnet"),
    )
    _poll_recovery_stubs(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await recover_poll_snapshot(EXE, ExecutionStore(), chat_url_hint=U)
    after_active = _active_snapshot()
    after_events = _registry_events(isolated_registry)
    _assert_observables(
        before_active,
        after_active,
        chat_url=U,
        before_events=before_events,
        after_events=after_events,
    )
    assert connect_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", ["free_host", "no_host_mint"], ids=["free_host", "no_host_mint"])
async def test_poll_recovery_label_read_failure_still_tears_down(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case_id: str,
) -> None:
    """A raising label read still closes the opened tab and releases a mint."""
    from claude_bundles import cdp_registry as reg

    ctxs: list[_FakeCtx] = []
    base = _connect_factory()

    async def _recording_connect(url: str) -> Any:
        pw, browser, ctx, _ = await base(url)
        ctxs.append(ctx)
        return pw, browser, ctx, _

    if case_id == "free_host":
        reg.register_lane(
            holder="free-host",
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
        minted_released = None
    else:
        host = reg.register_lane(
            holder="occupied-host",
            launch_chrome=_noop_launch,
            is_listening=lambda _p: False,
        )
        assert reg.bind_session_address(host.registration_id, chat_url=OTHER_U)
        minted_released = 1

    before_active = _active_snapshot()
    before_events = _registry_events(isolated_registry)
    monkeypatch.setattr("cdp_ask.followup_reattach.connect_cdp", _recording_connect)
    monkeypatch.setattr(
        "cdp_ask.followup_resolve.scan_lane_cse_urls",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr("cdp_ask.cse_session_harvest.emit", lambda _e: None)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _patch_holder_remote(monkeypatch)
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_scrape.harvest_turns",
        AsyncMock(
            return_value={
                "turns": [
                    {
                        "author": "assistant",
                        "text": "Body long enough for harvest acceptance.",
                        "ordinal": 1,
                    }
                ],
                "streaming": False,
                "stop": False,
                "tool_pause": False,
                "title": "Session",
                "loading": False,
            }
        ),
    )
    monkeypatch.setattr(
        "cdp_ask.poll_recovery.current_model_label",
        AsyncMock(side_effect=RuntimeError("label read failed")),
    )
    _poll_recovery_stubs(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="label read failed"):
        await recover_poll_snapshot(EXE, ExecutionStore(), chat_url_hint=U)

    after_active = _active_snapshot()
    after_events = _registry_events(isolated_registry)
    _assert_observables(
        before_active,
        after_active,
        chat_url=U,
        before_events=before_events,
        after_events=after_events,
        minted_released=minted_released,
    )
    pages = _pages_with_url(ctxs, U)
    assert pages
    assert all(page.closed for page in pages)


@pytest.mark.asyncio
async def test_default_attach_still_binds_where_harvest_does_not(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Harvest leaves the free host unbound; a default attach then binds it."""
    from claude_bundles import cdp_registry as reg

    host = reg.register_lane(
        holder="free-host",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    host_id = host.registration_id
    snap = copy.deepcopy(reg._store.load_active()[host_id])
    _patch_harvest_harness(monkeypatch, tmp_path, metadata_scrape=False)
    _patch_holder_remote(monkeypatch)
    store = ExecutionStore()
    await execute_harvest(
        HarvestRequest(chat_url=U, metadata_only=True),
        store,
    )
    assert reg._store.load_active()[host_id] == snap

    monkeypatch.setattr(
        "cdp_ask.followup_reattach.connect_cdp",
        _connect_factory(),
    )
    outcome = await ensure_cse_attached(U, holder="followup")
    assert outcome.ok
    assert outcome.page_only_borrow is False
    assert outcome.reattach_bound_chat_url == U
    assert reg.chat_url_for_registration(host_id) == U