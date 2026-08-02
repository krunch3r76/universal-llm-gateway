"""Unit tests for charter tick SMS format helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pager_notify.so_what import (
    compose_done_summary,
    format_closeout_pager,
    resolve_so_what_summary,
    tick_should_page,
)
from pager_notify.tick import (
    SMS_BODY_MAX,
    ClosedAttribution,
    format_closed_attribution,
    format_closed_human,
    format_tick_sms_body,
    format_tick_subject,
    task_hint_from_next_pickup,
)


def test_format_closed_attribution_example_shape() -> None:
    assert (
        format_closed_attribution("G3", "cdp/opus-5", "5975")
        == "G3@cdp/opus-5@5975"
    )
    assert (
        format_closed_attribution("G2", "cursor/grok-4.5", "6004")
        == "G2@cursor/grok-4.5@6004"
    )


def test_format_closed_human_includes_slug_and_task() -> None:
    line = format_closed_human(
        ClosedAttribution(
            gid="G1",
            executor_slug="cursor/grok-4.5",
            root_id="6037",
            thread_slug="pager-tick-endpoint-attribution",
            task_hint="implement harvest-close SMS attribution",
            source_ref="todo:pager-tick-endpoint-attribution",
        )
    )
    assert "G1 done" in line
    assert "pager-tick-endpoint-attribution" in line
    assert "#6037" in line
    assert "grok-4.5" in line
    assert "implement harvest-close" in line


def test_format_closed_human_prefers_so_what() -> None:
    line = format_closed_human(
        ClosedAttribution(
            gid="G5",
            executor_slug="cursor/grok-4.5",
            root_id="6004",
            thread_slug="ignored-slug",
            so_what="ULG: reliable closeout SMS with outcome titles",
        )
    )
    assert "ULG: reliable closeout SMS" in line
    assert "G5@6004" in line


def test_format_tick_subject_close_headline() -> None:
    subject = format_tick_subject(
        roots=1,
        in_flight=0,
        closed=[
            ClosedAttribution(
                gid="G1",
                executor_slug="cursor/grok-4.5",
                root_id="6037",
                thread_slug="pager-tick-endpoint-attribution",
            )
        ],
    )
    assert subject.startswith("G1 done — pager-tick-endpoint-attribution")
    assert "#6037" in subject


def test_format_tick_subject_so_what_headline() -> None:
    subject = format_tick_subject(
        roots=1,
        in_flight=0,
        closed=[
            ClosedAttribution(
                gid="G3",
                executor_slug="cdp/opus-5",
                root_id="5975",
                so_what="ULG: charter tick R-admit auto-wakes",
            )
        ],
    )
    assert subject.startswith("ULG: charter tick R-admit")
    assert "G3#5975" in subject


def test_format_tick_subject_idle_snapshot() -> None:
    assert (
        format_tick_subject(roots=3, in_flight=2, closed=None)
        == "Charter tick · 3 enrolled · 1 idle"
    )


def test_format_tick_sms_body_counts_only() -> None:
    body = format_tick_sms_body(
        roots=4,
        in_flight=1,
        admitted=1,
        skipped_by_reason={"window_in_flight": 1},
    )
    assert body == "conveyor en=4 live=1 idle=3 adm=1 skip=window_in_flight:1"
    assert len(body) <= SMS_BODY_MAX


def test_format_tick_sms_body_includes_closed_attributions() -> None:
    body = format_tick_sms_body(
        roots=2,
        in_flight=0,
        admitted=0,
        skipped_by_reason={},
        closed_attributions=[
            ClosedAttribution(
                gid="G3",
                executor_slug="cdp/opus-5",
                root_id="5975",
                thread_slug="charter-tick-kernel",
                task_hint="R-admit path-sim",
            ),
            ClosedAttribution(
                gid="G2",
                executor_slug="cursor/grok-4.5",
                root_id="6004",
                thread_slug="pager-v1",
            ),
        ],
    )
    assert "G3 done" in body
    assert "charter-tick-kernel" in body
    assert "|| conveyor en=2 live=0 idle=2 adm=0 skip=none" in body
    assert len(body) <= SMS_BODY_MAX


def test_format_tick_sms_body_truncates_to_budget() -> None:
    long_attrs = [
        ClosedAttribution(
            gid=f"G{i}",
            executor_slug="cursor/grok-4.5",
            root_id=str(6000 + i),
            thread_slug=f"very-long-charter-slug-name-{i}" * 3,
            task_hint="implement " * 20,
        )
        for i in range(8)
    ]
    body = format_tick_sms_body(
        roots=40,
        in_flight=0,
        admitted=0,
        skipped_by_reason={},
        closed_attributions=long_attrs,
        max_chars=SMS_BODY_MAX,
    )
    assert len(body) == SMS_BODY_MAX
    assert "conveyor en=40" in body


def test_task_hint_from_next_pickup_strips_gate_noise() -> None:
    hint = task_hint_from_next_pickup(
        [
            "G1 — implement · todo:pager-tick-endpoint-attribution · "
            "executor_lane: implement · detent=closed",
        ],
        "G1",
        source_ref="todo:pager-tick-endpoint-attribution",
    )
    assert "implement" in hint
    assert "executor_lane" not in hint
    assert "detent" not in hint


def test_tick_should_page_suppresses_idle() -> None:
    assert (
        tick_should_page(admitted=0, closed_count=0, skipped_by_reason={}) is False
    )
    assert (
        tick_should_page(
            admitted=0,
            closed_count=0,
            skipped_by_reason={"window_in_flight": 1},
        )
        is False
    )


def test_tick_should_page_on_admit_or_close_or_interesting_skip() -> None:
    assert tick_should_page(admitted=1, closed_count=0, skipped_by_reason={})
    assert tick_should_page(admitted=0, closed_count=1, skipped_by_reason={})
    assert tick_should_page(
        admitted=0,
        closed_count=0,
        skipped_by_reason={"no_progress:consult_stall": 1},
    )


def test_standing_skip_signature_only_standing() -> None:
    from pager_notify.so_what import standing_skip_signature

    assert standing_skip_signature({"blocked": 1}) == "blocked:1"
    assert (
        standing_skip_signature({"stopped:admission_transport_error": 1})
        == "stopped:admission_transport_error:1"
    )
    assert standing_skip_signature({"window_in_flight": 1}) is None
    assert standing_skip_signature({"blocked": 1, "window_in_flight": 2}) is None


@pytest.mark.asyncio
async def test_notify_tick_complete_dedupes_standing_blocked(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pager_notify import tick as tick_mod
    from pager_notify.state import claim_tick_standing_page

    monkeypatch.setenv("PAGER_NOTIFY_STATE_DIR", str(tmp_path))
    pager = AsyncMock(return_value=True)
    monkeypatch.setattr(tick_mod, "notify_pager", pager)

    kwargs = {
        "roots": 2,
        "in_flight": 0,
        "admitted": 0,
        "skipped_by_reason": {"blocked": 1},
    }
    assert await tick_mod.notify_tick_complete(**kwargs) is True
    assert await tick_mod.notify_tick_complete(**kwargs) is False
    assert pager.await_count == 1
    # Signature change pages again.
    assert (
        await tick_mod.notify_tick_complete(
            **{**kwargs, "skipped_by_reason": {"stopped:x": 1}}
        )
        is True
    )
    assert pager.await_count == 2
    assert claim_tick_standing_page("stopped:x:1") is False


def test_compose_done_summary_preserves_so_what() -> None:
    assert compose_done_summary("ULG: wake consults") == "DONE — ULG: wake consults"
    assert compose_done_summary("DONE — ULG: wake consults").startswith("DONE —")


def test_resolve_so_what_from_body() -> None:
    assert (
        resolve_so_what_summary(None, "TYPE: DIRECTIVE\nso_what: ULG gains X\n")
        == "ULG gains X"
    )
    assert resolve_so_what_summary("explicit", "so_what: ignored") == "explicit"


def test_format_closeout_pager_leads_with_so_what() -> None:
    subject, body = format_closeout_pager(
        status="complete",
        thread_id="6075",
        summary="ULG: operator-proxy posture accelerates vision",
        dispatch_id="abc",
        fallback_subject="ignored when so-what present",
    )
    assert subject.startswith("ULG: operator-proxy")
    assert "CLOSEOUT complete" in subject
    assert "bus:6075" in body
    assert "ULG: operator-proxy" in body
    assert "ignored when so-what" not in subject


def test_format_closeout_pager_falls_back_to_job_subject() -> None:
    subject, body = format_closeout_pager(
        status="complete",
        thread_id="6655",
        summary=None,
        dispatch_id="auto-abc",
        fallback_subject="fix ledger age race",
    )
    assert subject.startswith("fix ledger age race — CLOSEOUT complete")
    assert "bus:6655" in body
    assert "fix ledger age race" in body


def test_format_closeout_pager_machine_fallback_when_empty() -> None:
    subject, body = format_closeout_pager(
        status="complete",
        thread_id="6655",
        summary="",
        dispatch_id="auto-abc",
    )
    assert subject == "CLOSEOUT complete bus:6655"
    assert "bus:6655" in body
