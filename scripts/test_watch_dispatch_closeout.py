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


def _run_main_capture_page(
    wdc: Any, monkeypatch: pytest.MonkeyPatch, closeout_body: str, argv: list[str]
) -> dict[str, str]:
    """Drive main() to one closeout with every bus/event/send seam stubbed."""
    sent: dict[str, str] = {}
    client = mock.MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(wdc, "_token", lambda: "t")
    monkeypatch.setattr(wdc, "_bus_client", lambda token: client)
    monkeypatch.setattr(wdc, "_resolve_execution_id", lambda **k: "exec-1")
    monkeypatch.setattr(wdc, "require_producer_declaration", lambda *a, **k: "")
    monkeypatch.setattr(wdc, "_worker_completed", lambda w: {"outcome": "success"})
    monkeypatch.setattr(
        wdc,
        "_wait_closeout",
        lambda c, **k: {"complete": True, "qualifying_reply_turn": 2},
    )
    monkeypatch.setattr(
        wdc,
        "_fetch_closeout_turn",
        lambda c, t, n: {"subject": "cursor-sdk CLOSEOUT x", "body": closeout_body},
    )
    monkeypatch.setattr(
        wdc, "_parent_harvest_subject", lambda c, p, **k: "parent harvest subj"
    )

    def _capture(*, subject: str, body: str, tag: str) -> bool:
        sent.update(subject=subject, body=body, tag=tag)
        return True

    monkeypatch.setattr(wdc, "_page_operator", _capture)
    monkeypatch.setattr(wdc.sys, "argv", ["wdc", *argv])
    assert wdc.main() == 0
    return sent


@pytest.mark.offline
def test_main_routes_summary_closeout_through_page_text(
    wdc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        '{"schema_version": 1, "summary": "MISSION-16930", "work_outcome": "complete"}'
    )
    sent = _run_main_capture_page(
        wdc, monkeypatch, body, ["--thread", "16416", "--dispatch-id", "d-1"]
    )
    assert "MISSION-16930" in sent["body"]
    assert "9938" not in sent["body"]


@pytest.mark.offline
def test_main_parent_subj_with_non_json_closeout_uses_generic_body(
    wdc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _run_main_capture_page(
        wdc,
        monkeypatch,
        "CLOSEOUT prose, not JSON",
        ["--thread", "16416", "--dispatch-id", "d-1", "--parent-thread", "16960"],
    )
    assert sent["subject"] == "parent harvest subj"
    assert sent["body"].startswith("thread 16416 · success")


@pytest.mark.offline
def test_main_come_to_ide_body_generic_return_hint(
    wdc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _run_main_capture_page(
        wdc,
        monkeypatch,
        "CLOSEOUT prose, not JSON",
        [
            "--thread",
            "16416",
            "--dispatch-id",
            "d-1",
            "--come-to-ide",
            "--label",
            "39341",
        ],
    )
    assert sent["subject"] == "COME TO IDE — 39341"
    assert " · return to Cursor IDE" in sent["body"]
    assert "529" not in sent["body"]
    assert "friction" not in sent["body"]


@pytest.mark.offline
def test_unparseable_exit_code_does_not_raise(wdc: Any) -> None:
    closeout = {"summary": "s", "verification": [{"command": "c", "exit_code": "n/a"}]}
    _subj, body = wdc._arc_outcome_line(closeout, label="L", thread_id="1")
    assert "checks_failed" not in body


@pytest.mark.offline
def test_source_ref_is_not_labelled_work_key(wdc: Any) -> None:
    closeout = {
        "summary": "s",
        "source_ref": "workspaces://x/tmp/reviews/closeouts/a.md",
    }
    _subj, body = wdc._arc_outcome_line(closeout, label="L", thread_id="1")
    assert "work_key:" not in body
