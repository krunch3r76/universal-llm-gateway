"""Tests for ULG story wire polling projector."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.git_integration_worker.ulg_story_projector.allowlist import (
    SIGNAL_ALLOWLIST,
)
from services.git_integration_worker.ulg_story_projector.checkpoint import (
    ProjectorCheckpoint,
    checkpoint_path,
    load_checkpoint,
    save_checkpoint,
)
from services.git_integration_worker.ulg_story_projector.projector import run_projector_once
from services.git_integration_worker.ulg_story_projector.render import (
    PURPOSE_RENDER_MAX,
    envelope_mode,
    render_event_line,
    truncate_purpose_for_render,
)
from systems.frontier_consult.story_wire import (
    ASKED_BY_UNRESOLVED,
    PURPOSE_UNSTATED,
)


def _payload(**kwargs: object) -> dict[str, object]:
    base = {
        "dispatch_id": "auto-4ee49ad8686d",
        "thread_id": "6221",
        "execution_id": "exec-4ee49ad8686d",
        "story_id": "req-4ee49ad8686d",
        "asked_by": "web-anthropic",
        "purpose": "event-envelope work",
    }
    base.update(kwargs)
    return base


def test_allowlist_excludes_waived_refusal() -> None:
    assert "frontier.sdk.auto.empty_directive_scope_waived" not in SIGNAL_ALLOWLIST


def test_render_closeout_milestone() -> None:
    line = render_event_line(
        seq=100,
        signal="frontier.sdk.closeout.relayed",
        payload=_payload(
            closeout_status="complete",
            receipt_path="tmp/reviews/closeouts/auto-4ee49ad8686d.md",
        ),
    )
    assert line is not None
    assert "cursor-sdk finished event-envelope work" in line
    assert "Claude-web (operator seat)" in line
    assert "receipt at tmp/reviews/closeouts/auto-4ee49ad8686d.md" in line
    assert "[seq:100 story:req-4ee49ad8686d dispatch:auto-4ee49ad8686d]" in line
    assert not line.startswith("Attention:")


def test_render_partial_work_production_specimen_milestone() -> None:
    line = render_event_line(
        seq=103,
        signal="frontier.sdk.closeout.partial_work.production_specimen",
        payload=_payload(
            envelope_turn=2489,
            closeout_source="section2_sidecar",
            contract="implement",
        ),
    )
    assert line is not None
    assert "recorded partial:work specimen for event-envelope work" in line
    assert "(turn 2489)" in line
    assert "dispatch auto-4ee49ad8686d" in line
    assert not line.startswith("Attention:")


def test_render_completed_milestone() -> None:
    line = render_event_line(
        seq=101,
        signal="frontier.sdk.worker.completed",
        payload=_payload(duration_s=42),
    )
    assert line is not None
    assert "cursor-sdk finished event-envelope work" in line
    assert "in 42s" in line


def test_render_failed_attention() -> None:
    line = render_event_line(
        seq=102,
        signal="frontier.sdk.worker.failed",
        payload=_payload(error="worker unreachable"),
    )
    assert line is not None
    assert line.startswith("Attention:")
    assert "failed on event-envelope work" in line
    assert "worker unreachable" in line


def test_render_failed_pre_envelope_stays_attention() -> None:
    line = render_event_line(
        seq=1021,
        signal="frontier.sdk.worker.failed",
        payload={
            "dispatch_id": "auto-fail01",
            "thread_id": "6221",
            "error": "connection refused",
        },
    )
    assert line is not None
    assert line.startswith("Attention:")
    assert "failed on a task for an operator seat" in line
    assert "connection refused" in line


def test_render_pre_envelope_routine_no_attention() -> None:
    line = render_event_line(
        seq=103,
        signal="frontier.sdk.worker.dispatched",
        payload={
            "thread_id": "6221",
            "dispatch_id": "auto-old01",
        },
    )
    assert line is not None
    assert not line.startswith("Attention:")
    assert envelope_mode({"thread_id": "6221"}) == "pre_envelope"
    assert "An operator seat dispatched cursor-sdk on thread 6221." in line
    assert "asked-by not recorded" not in line
    assert "(unstated)" not in line


def test_render_caller_omitted_sentinel_attention() -> None:
    payload = {
        "thread_id": "6223",
        "dispatch_id": "auto-new01",
        "story_id": "req-new01",
        "asked_by": ASKED_BY_UNRESOLVED,
        "purpose": PURPOSE_UNSTATED,
    }
    assert envelope_mode(payload) == "caller_omitted"
    line = render_event_line(
        seq=104,
        signal="frontier.sdk.worker.dispatched",
        payload=payload,
    )
    assert line is not None
    assert line.startswith("Attention:")
    assert "purpose not stated" in line
    assert "asked-by not recorded" in line


def test_render_completed_grammar_and_duration_rounding() -> None:
    line = render_event_line(
        seq=105,
        signal="frontier.sdk.worker.completed",
        payload={
            "thread_id": "6221",
            "dispatch_id": "auto-old02",
            "duration_s": 71.089,
        },
    )
    assert line is not None
    assert not line.startswith("Attention:")
    assert "cursor-sdk finished a task for an operator seat in 71.1s." in line

    omitted = render_event_line(
        seq=106,
        signal="frontier.sdk.worker.completed",
        payload=_payload(duration_s=71.089, purpose=PURPOSE_UNSTATED),
    )
    assert omitted is not None
    assert omitted.startswith("Attention:")
    assert "finished a task (purpose not stated)" in omitted
    assert "in 71.1s" in omitted


def test_render_full_envelope_milestone() -> None:
    line = render_event_line(
        seq=107,
        signal="frontier.sdk.worker.completed",
        payload=_payload(duration_s=42.0),
    )
    assert line is not None
    assert not line.startswith("Attention:")
    assert "cursor-sdk finished event-envelope work for Claude-web (operator seat) in 42s." in line


def test_render_malformed_payload_becomes_parse_failure_shape() -> None:
    from services.git_integration_worker.ulg_story_projector.render import (
        render_parse_failure,
    )

    line = render_parse_failure(seq=9, signal="frontier.sdk.worker.completed", reason="boom")
    assert "could not render" in line
    assert "[seq:9 story:- dispatch:-]" in line


def test_idempotent_restart_no_duplicate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal_dir = tmp_path / "notes/system/journal/ulg-story"
    journal_dir.mkdir(parents=True)
    shard = journal_dir / "2026-07.md"
    shard.write_text(
        "cursor-sdk finished alpha for Claude-web (operator seat). "
        "[seq:50 story:s1 dispatch:d1]\n",
        encoding="utf-8",
    )
    ckpt = tmp_path / "ulg-story-projector-checkpoint.json"
    ckpt.write_text('{"last_seq": 40, "epoch_written": true, "updated_at": ""}\n')

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.journal.cortex_files_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.events_query_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.query_oldest_live_seq",
        lambda: 50,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.query_events_since_seq",
        lambda since_seq, limit=200: (
            [
                {
                    "seq": 50,
                    "signal": "frontier.sdk.worker.completed",
                    "ts_unix_ms": 1_783_000_000_000,
                    "payload": _payload(purpose="alpha"),
                }
            ]
            if since_seq < 50
            else []
        ),
    )

    first = run_projector_once()
    text_after_first = shard.read_text(encoding="utf-8")
    assert text_after_first.count("[seq:50") == 1
    assert first["processed"] == 0

    second = run_projector_once()
    text_after_second = shard.read_text(encoding="utf-8")
    assert text_after_second.count("[seq:50") == 1
    assert second["processed"] == 0
    assert load_checkpoint().last_seq == 50


def test_gap_detection_writes_attention_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.journal.cortex_files_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.events_query_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.query_oldest_live_seq",
        lambda: 5000,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.query_event_timestamp",
        lambda seq: 1_700_000_000_000 if seq == 1000 else 1_800_000_000_000,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.query_events_since_seq",
        lambda since_seq, limit=200: [],
    )
    save_checkpoint(
        ProjectorCheckpoint(last_seq=1000, epoch_written=False, updated_at=""),
    )

    result = run_projector_once()
    shard = (
        tmp_path
        / "notes/system/journal/ulg-story"
        / f"{datetime.now(UTC).strftime('%Y-%m')}.md"
    )
    text = shard.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "Epoch:" in text
    assert "retention gap" in text
    assert "1001–4999" in text
    assert load_checkpoint().last_seq == 4999


def test_monthly_shard_rollover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.journal.cortex_files_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.events_query_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.query_oldest_live_seq",
        lambda: 1,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.ulg_story_projector.projector.query_events_since_seq",
        lambda since_seq, limit=200: [
            {
                "seq": 1,
                "signal": "frontier.sdk.worker.dispatched",
                "ts_unix_ms": 1_735_689_600_000,  # 2025-01-01
                "payload": _payload(purpose="jan work"),
            },
            {
                "seq": 2,
                "signal": "frontier.sdk.worker.completed",
                "ts_unix_ms": 1_738_368_000_000,  # 2025-02-01 UTC
                "payload": _payload(purpose="feb work"),
            },
        ]
        if since_seq == 0
        else [],
    )

    run_projector_once()
    jan = tmp_path / "notes/system/journal/ulg-story/2025-01.md"
    feb = tmp_path / "notes/system/journal/ulg-story/2025-02.md"
    assert jan.is_file()
    assert feb.is_file()
    assert "jan work" in jan.read_text(encoding="utf-8")
    assert "feb work" in feb.read_text(encoding="utf-8")


def test_checkpoint_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    save_checkpoint(ProjectorCheckpoint(last_seq=77, epoch_written=True, updated_at="t"))
    loaded = load_checkpoint()
    assert loaded.last_seq == 77
    assert loaded.epoch_written is True
    assert checkpoint_path().is_file()


_LONG_PURPOSE = (
    'A synthesized §2 envelope may report uncertainty about its own parsing, '
    'but must never assert a false negative about the world. Not "unauthored", '
    'not "none captured", not a per-field ref-flatten that discards substance '
    "the relay is already holding in memory."
)


def test_truncate_purpose_at_word_boundary_under_cap() -> None:
    truncated = truncate_purpose_for_render(_LONG_PURPOSE)
    assert len(truncated) <= PURPOSE_RENDER_MAX
    assert truncated.endswith("parsing")
    assert "…" not in truncated
    assert "..." not in truncated


def test_truncate_purpose_short_unchanged() -> None:
    short = "event-envelope work"
    assert truncate_purpose_for_render(short) == short


def test_render_long_purpose_truncated_grammatical() -> None:
    line = render_event_line(
        seq=51172326,
        signal="frontier.sdk.worker.completed",
        payload=_payload(purpose=_LONG_PURPOSE, duration_s=194),
    )
    assert line is not None
    assert len(_LONG_PURPOSE) > PURPOSE_RENDER_MAX
    assert "false negative about the world" not in line
    assert "unauthored" not in line
    assert " for Claude-web (operator seat) in 194s." in line
    assert line.startswith("cursor-sdk finished A synthesized §2 envelope")
    assert "[seq:51172326" in line


def test_render_short_purpose_byte_identical() -> None:
    payload = _payload(duration_s=42.0)
    line = render_event_line(
        seq=107,
        signal="frontier.sdk.worker.completed",
        payload=payload,
    )
    expected = (
        "cursor-sdk finished event-envelope work for Claude-web (operator seat) "
        "in 42s. [seq:107 story:req-4ee49ad8686d dispatch:auto-4ee49ad8686d]"
    )
    assert line == expected


def test_render_does_not_mutate_payload_purpose() -> None:
    payload = _payload(purpose=_LONG_PURPOSE, duration_s=194)
    original = payload["purpose"]
    render_event_line(
        seq=51172326,
        signal="frontier.sdk.worker.completed",
        payload=payload,
    )
    assert payload["purpose"] == original
    assert len(str(payload["purpose"])) > PURPOSE_RENDER_MAX


def test_render_control_case_short_intent_under_cap() -> None:
    """This dispatch's intent line is deliberately short — control case."""
    purpose = (
        "Cap purpose at render so the wire is skimmable; "
        "re-render existing long lines."
    )
    assert len(purpose) <= PURPOSE_RENDER_MAX
    line = render_event_line(
        seq=999,
        signal="frontier.sdk.worker.completed",
        payload=_payload(purpose=purpose, duration_s=120),
    )
    assert line is not None
    assert purpose in line
    assert len(purpose) == len(truncate_purpose_for_render(purpose))


def test_truncate_purpose_unclosed_backtick() -> None:
    purpose = (
        "6205 turn 13's Next-pickup hands `G1 todo:operator-proxy-closeout-section2-relay-recurrence` "
        "to whoever reads the root next. That todo is now closed (`workflow_state=done`, a:26817), "
        "landed and dispositioned on this lane."
    )
    truncated = truncate_purpose_for_render(purpose)
    assert len(truncated) <= PURPOSE_RENDER_MAX
    assert "`" not in truncated
    assert truncated.endswith("hands")
