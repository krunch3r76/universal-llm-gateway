"""Unit tests for closeout pager dedupe (friction-26795 S2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pager_notify.closeout import notify_closeout_complete
from pager_notify.state import claim_closeout_page


@pytest.fixture
def pager_state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAGER_NOTIFY_STATE_DIR", str(tmp_path))
    return tmp_path


def test_claim_closeout_page_first_claim_true(pager_state_dir) -> None:
    assert claim_closeout_page("5899", "closed", now=1000.0) is True


def test_claim_closeout_page_second_within_ttl_false(pager_state_dir) -> None:
    assert claim_closeout_page("5899", "closed", now=1000.0) is True
    assert claim_closeout_page("5899", "closed", now=1100.0) is False


def test_claim_closeout_page_different_status_true(pager_state_dir) -> None:
    assert claim_closeout_page("5899", "closed", now=1000.0) is True
    assert claim_closeout_page("5899", "blocked", now=1001.0) is True


def test_claim_closeout_page_after_ttl_expiry_true(pager_state_dir) -> None:
    assert claim_closeout_page("5899", "closed", now=1000.0, ttl_s=300.0) is True
    assert claim_closeout_page("5899", "closed", now=1299.0, ttl_s=300.0) is False
    assert claim_closeout_page("5899", "closed", now=1300.0, ttl_s=300.0) is True


def test_claim_closeout_page_normalizes_status(pager_state_dir) -> None:
    assert claim_closeout_page("5899", " CLOSED ", now=1000.0) is True
    assert claim_closeout_page("5899", "closed", now=1001.0) is False


def test_closeout_dedupe_shared_across_dispatch_home_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dedupe state must not be scoped to per-dispatch HOME overlay."""
    shared = tmp_path / "shared-pager-state"
    monkeypatch.setenv("PAGER_NOTIFY_STATE_DIR", str(shared))
    monkeypatch.setenv("HOME", "/tmp/fake/cursor-dispatch-homes/auto-x-home")
    assert claim_closeout_page("6692-overlay", "closed", now=1000.0) is True
    monkeypatch.setenv("HOME", "/tmp/fake/cursor-dispatch-homes/auto-y-home")
    assert claim_closeout_page("6692-overlay", "closed", now=1001.0) is False


@pytest.mark.asyncio
async def test_notify_closeout_complete_dedupes_pager(
    pager_state_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = AsyncMock(return_value="prior summary")
    patch = AsyncMock()
    pager = AsyncMock(return_value=True)
    monkeypatch.setattr("pager_notify.closeout._fetch_summary", fetch)
    monkeypatch.setattr("pager_notify.closeout._patch_summary", patch)
    monkeypatch.setattr("pager_notify.closeout.notify_pager", pager)

    kwargs = {
        "thread_id": "5899",
        "status": "closed",
        "dispatch_id": "auto-b80aa",
        "closeout_body": "",
        "sdk_body": "",
    }
    assert await notify_closeout_complete(**kwargs) is True
    assert await notify_closeout_complete(**kwargs) is False

    assert pager.await_count == 1
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_notify_closeout_complete_uses_job_subject_when_no_so_what(
    pager_state_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = AsyncMock(return_value="")
    patch = AsyncMock()
    pager = AsyncMock(return_value=True)
    monkeypatch.setattr("pager_notify.closeout._fetch_summary", fetch)
    monkeypatch.setattr("pager_notify.closeout._patch_summary", patch)
    monkeypatch.setattr("pager_notify.closeout.notify_pager", pager)

    assert (
        await notify_closeout_complete(
            thread_id="6655",
            status="complete",
            dispatch_id="auto-xyz",
            job_subject="fix ledger age race",
        )
        is True
    )
    subject, body = pager.await_args.args[:2]
    assert subject.startswith("fix ledger age race — CLOSEOUT complete")
    assert "fix ledger age race" in body
    patch.assert_not_awaited()
