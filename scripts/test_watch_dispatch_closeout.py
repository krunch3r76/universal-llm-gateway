"""Offline tests for watch-dispatch-closeout pager copy (friction 39316)."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

_SCRIPT = Path(__file__).resolve().parent / "watch-dispatch-closeout.py"

_MISSION_16930 = (
    "todo:maestro-cse-wake-route-and-chat-url-fallback — route CSE wake through "
    "maestro and add chat URL fallback when the primary hop is unavailable."
)


def _load_wdc() -> Any:
    loader = SourceFileLoader("watch_dispatch_closeout_t", str(_SCRIPT))
    spec = importlib.util.spec_from_loader("watch_dispatch_closeout_t", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def wdc() -> Any:
    return _load_wdc()


@pytest.mark.offline
def test_t1_closeout_summary_is_mission_not_9938_banner(wdc: Any) -> None:
    closeout = {
        "schema_version": 1,
        "summary": _MISSION_16930,
        "work_outcome": "complete",
        "source_ref": "todo:maestro-cse-wake-route-and-chat-url-fallback",
        "status": "finished",
    }
    _subj, body = wdc._arc_outcome_line(closeout, label="16930 hop", thread_id="16416")
    assert _MISSION_16930 in body
    assert "9938" not in body
    assert "event-store durability" not in body


@pytest.mark.offline
def test_t2_summary_may_name_9938_without_injected_nfs_vision(wdc: Any) -> None:
    closeout = {
        "schema_version": 1,
        "summary": "Follow up on thread 9938 event-db recovery notes.",
        "work_outcome": "complete",
        "status": "finished",
    }
    _subj, body = wdc._arc_outcome_line(closeout, label="arc", thread_id="17000")
    assert "9938" in body
    assert "event-store durability" not in body


@pytest.mark.offline
def test_t3_parent_subj_only_uses_generic_body(wdc: Any) -> None:
    page_calls: list[tuple[str, str, str]] = []

    def _stub_page(*, subject: str, body: str, tag: str) -> bool:
        page_calls.append((subject, body, tag))
        return True

    with mock.patch.object(wdc, "_page_operator", side_effect=_stub_page):
        subj, body = wdc._closeout_page_text(
            closeout_json={},
            parent_subj="cursor-sdk CLOSEOUT — nested harvest",
            label="16930",
            thread_id="16416",
            outcome="complete",
            elapsed_s=120.0,
            duration=90,
            model="cursor/composer-2.5",
            worker_subject="",
        )
    assert subj == "cursor-sdk CLOSEOUT — nested harvest"
    assert "thread 16416" in body
    assert "9938" not in body
    assert "event-store durability" not in body
    assert page_calls == []


@pytest.mark.offline
def test_t4_failed_verification_lists_command_not_fixed_thread(wdc: Any) -> None:
    closeout = {
        "schema_version": 1,
        "summary": "Land closeout pager fix.",
        "work_outcome": "checks_failed",
        "status": "finished",
        "verification": [
            {
                "command": "ruff check scripts/watch-dispatch-closeout.py",
                "exit_code": 1,
            },
            {
                "command": "pytest scripts/test_watch_dispatch_closeout.py -q",
                "exit_code": 0,
            },
        ],
    }
    _subj, body = wdc._arc_outcome_line(closeout, label="39316", thread_id="16973")
    assert "checks_failed: ruff check scripts/watch-dispatch-closeout.py" in body
    assert "thread 9938" not in body.lower()


@pytest.mark.offline
def test_t5_grep_pin_event_store_durability_absent_from_script() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "event-store durability" not in text


@pytest.mark.offline
def test_page_operator_stub_never_invoked_on_offline_helpers(wdc: Any) -> None:
    """Pager transport stays offline — helpers must not call _page_operator."""
    with mock.patch.object(wdc, "_page_operator") as page_mock:
        wdc._arc_outcome_line(
            {"schema_version": 1, "summary": "x", "status": "finished"},
            label="L",
            thread_id="1",
        )
        wdc._closeout_page_text(
            closeout_json={},
            parent_subj="parent",
            label="L",
            thread_id="1",
            outcome="complete",
            elapsed_s=1.0,
            duration=None,
            model="",
            worker_subject="",
        )
    page_mock.assert_not_called()
