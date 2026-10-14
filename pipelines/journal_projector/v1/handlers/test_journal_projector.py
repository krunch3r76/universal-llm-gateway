"""Acceptance tests for journal-projector L2 (hermetic)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from claude_bundles.journal_lesson import writer_mode
from systems.pipeline.core.execution.async_tracker import (
    PipelineExecutionTracker,
    TrackerCapacityError,
)
from systems.pipeline.core.execution.async_tracker import lifecycle as lifecycle_mod
from systems.pipeline.core.handlers.frontier_dispatch.request import (
    resolve_system_prompt,
)
from systems.pipeline.core.handlers.step_output import StepOutput
from systems.pipeline.core.step_config import StepConfig
from systems.proxy.routers.api.pipelines_dispatch import (
    DispatchRequest,
    ResultDeliveryConfig,
    admit_dispatch,
)

from pipelines.journal_projector.v1.handlers.append_closeout import (
    append_journal_from_closeout,
)
from pipelines.journal_projector.v1.handlers.parse_lesson import (
    JournalProjectorParseHandler,
)
from systems.pipeline.core.execution.errors import UnknownPipelineOptionsError
from systems.pipeline.core.handlers.frontier_dispatch.admission_checks import (
    reject_unknown_runtime_options,
)
from systems.pipeline.core.handlers.frontier_dispatch.handler import (
    FrontierDispatchHandler,
)
from pipelines.journal_projector.v1.handlers.projector_guard import (
    reject_projector_wire_model,
)
from pipelines.journal_projector.v1.handlers.projector_status import (
    closeout_indicates_provider_error,
    projection_poll_is_delay,
    seat_may_fallback_after_projector_failure,
)


def test_runtime_options_source_turn_id_rejected_at_admission() -> None:
    """Production trigger must not pass dedupe key via runtime_options (one field per run)."""
    step = SimpleNamespace(id="project")
    context = SimpleNamespace(
        options={"model": "cursor/composer-2.5"},
        runtime_options={
            "model": "cursor/composer-2.5",
            "source_turn_id": "agent-bus:16671#1",
        },
    )
    with pytest.raises(UnknownPipelineOptionsError) as exc_info:
        reject_unknown_runtime_options(
            step,
            context,
            FrontierDispatchHandler._ACCEPTED_RUNTIME_OPTION_KEYS,
        )
    assert "source_turn_id" in str(exc_info.value)


def _disposition_with_lesson(
    turn_id: str | None, *, lesson: str = "poll with poll_hint unchanged"
) -> str:
    lines = ["TYPE: DISPOSITION", "status: complete"]
    if turn_id is not None:
        lines.append(f"source_turn_id: {turn_id}")
    lines.append(f"lesson: {lesson}")
    return "\n".join(lines) + "\n"


@pytest.mark.asyncio
async def test_parse_missing_turn_id_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = JournalProjectorParseHandler()
    body = _disposition_with_lesson(None)
    context = SimpleNamespace(options={}, messages=[{"role": "user", "content": body}])
    out = await handler.execute(SimpleNamespace(id="parse"), context)
    assert out.json is not None
    assert out.json.get("has_lesson") is False
    assert out.json.get("reason") == "no_turn_id"

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    journal = tmp_path / "notes/system/maestro/journal.md"
    _seed_projector_journal(journal)
    closeout = "RULE: [brief] Should not land.\n"
    result = append_journal_from_closeout(closeout)
    assert result.skipped is True
    assert result.skipped_reason == "no_turn_id"
    assert "Should not land" not in journal.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_parse_through_append_turn_id_from_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    journal = tmp_path / "notes/system/maestro/journal.md"
    _seed_projector_journal(journal)
    handler = JournalProjectorParseHandler()

    turn_a = "agent-bus:16671#101"
    body_a = _disposition_with_lesson(turn_a)
    out_a = await handler.execute(
        SimpleNamespace(id="parse"),
        SimpleNamespace(options={}, messages=[{"role": "user", "content": body_a}]),
    )
    assert out_a.json is not None
    assert out_a.json.get("has_lesson") is True
    assert out_a.json.get("turn_id") == turn_a

    closeout_a = f"RULE: [brief] First lesson.\nsource_turn_id: {turn_a}\n"
    first = append_journal_from_closeout(closeout_a)
    assert first.skipped is False
    assert turn_a in journal.read_text(encoding="utf-8")

    turn_b = "agent-bus:16671#102"
    body_b = _disposition_with_lesson(turn_b, lesson="second distinct lesson")
    out_b = await handler.execute(
        SimpleNamespace(id="parse"),
        SimpleNamespace(options={}, messages=[{"role": "user", "content": body_b}]),
    )
    assert out_b.json is not None
    assert out_b.json.get("turn_id") == turn_b

    closeout_b = f"RULE: [bus] Second lesson.\nsource_turn_id: {turn_b}\n"
    second = append_journal_from_closeout(closeout_b)
    assert second.skipped is False
    assert second.skipped_reason != "deduped"
    text = journal.read_text(encoding="utf-8")
    assert turn_a in text
    assert turn_b in text


def test_projector_rejects_anthropic_and_other_models_pool() -> None:
    with pytest.raises(ValueError, match="anthropic"):
        reject_projector_wire_model("anthropic/claude-sonnet-5")
    with pytest.raises(ValueError, match="other-models"):
        reject_projector_wire_model("cursor/claude-opus-5")
    with pytest.raises(ValueError, match="composer-2.5-fast"):
        reject_projector_wire_model("cursor/composer-2.5-fast")
    reject_projector_wire_model("cursor/composer-2.5")


@pytest.mark.asyncio
async def test_no_lesson_skips_model(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_sdk(**_k: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"execution_id": "x"}

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_generate",
        fake_sdk,
    )
    handler = JournalProjectorParseHandler()
    context = SimpleNamespace(
        options={},
        messages=[{"role": "user", "content": "TYPE: DISPOSITION\n\nno lesson here"}],
    )
    step = SimpleNamespace(id="parse")
    out = await handler.execute(step, context)
    assert out.json is not None
    assert out.json.get("has_lesson") is False
    assert called is False


def _seed_projector_journal(journal: Path) -> None:
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("- projector_live: dispatch-test\n\n", encoding="utf-8")


def test_project_step_system_prompt_binds_turn_id_and_skill_cite() -> None:
    step = StepConfig(
        id="project",
        type="frontier_dispatch_v1",
        system_prompt=(
            "Output contract: line-start RULE: with facet tags; "
            "source_turn_id: {turn_id}. Use the `journal-digest` skill."
        ),
        handler_inputs={"turn_id": "parse.json.turn_id"},
    )
    context = SimpleNamespace(
        options={},
        source=SimpleNamespace(),
        outputs={
            "parse": StepOutput(
                raw="",
                json={"has_lesson": True, "turn_id": "agent-bus:16671#79"},
            )
        },
        messages=None,
        source_text="",
    )
    system = resolve_system_prompt(step, context)
    assert "agent-bus:16671#79" in system
    assert "journal-digest" in system
    assert "RULE:" in system

    context.options = {
        "system": "pipeline_options.system override must win over step prompt."
    }
    assert resolve_system_prompt(step, context) == context.options["system"]


def test_append_reads_closeout_not_admit_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    journal = tmp_path / "notes/system/maestro/journal.md"
    _seed_projector_journal(journal)

    closeout = (
        "status: complete\n\n"
        "RULE: [brief] Always verify before done.\n"
        "source_turn_id: turn-9\n"
    )
    result = append_journal_from_closeout(closeout, turn_id="turn-9")
    assert result.ok is True
    assert result.skipped is False
    text = journal.read_text(encoding="utf-8")
    assert "RULE: [brief]" in text
    assert "source_turn_id: turn-9" in text

    admit_only = json.dumps(
        {"execution_id": "exec-1", "poll_hint": {"tool": "wait", "arguments": {}}}
    )
    before = journal.read_text(encoding="utf-8")
    skip = append_journal_from_closeout(admit_only)
    assert skip.ok is True
    assert skip.skipped is True
    assert skip.skipped_reason == "admit_envelope"
    assert journal.read_text(encoding="utf-8") == before


def test_append_fails_closed_without_projector_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    journal = tmp_path / "notes/system/maestro/journal.md"
    journal.parent.mkdir(parents=True)
    journal.write_text("seat-owned journal with no marker\n", encoding="utf-8")
    closeout = "RULE: [brief] Should not append.\nsource_turn_id: turn-seat\n"
    result = append_journal_from_closeout(closeout, turn_id="turn-seat")
    assert result.ok is False
    assert result.error is not None
    assert result.error.get("kind") == "projector_not_live"
    assert "seat-owned" in journal.read_text(encoding="utf-8")


def test_append_dedupes_turn_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    journal = tmp_path / "notes/system/maestro/journal.md"
    _seed_projector_journal(journal)
    closeout = "RULE: [bus] Dedupe me.\nsource_turn_id: turn-dup\n"
    first = append_journal_from_closeout(closeout, turn_id="turn-dup")
    assert first.skipped is False
    second = append_journal_from_closeout(closeout, turn_id="turn-dup")
    assert second.skipped is True
    assert second.skipped_reason == "deduped"


def test_append_conflict_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    journal = tmp_path / "notes/system/maestro/journal.md"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        "- projector_live: dispatch-cas\n\nmutated-on-disk\n",
        encoding="utf-8",
    )

    closeout = "RULE: [land] Conflict.\nsource_turn_id: turn-cas\n"

    import pipelines.journal_projector.v1.handlers.append_closeout as append_mod

    def stale_read(uri: str = append_mod.MAESTRO_JOURNAL_URI):
        text = "- projector_live: cas-test\n\nseed\n"
        return text, hashlib.sha256(b"seed\n").hexdigest()

    monkeypatch.setattr(append_mod, "read_journal_text", stale_read)
    result = append_journal_from_closeout(closeout, turn_id="turn-cas")
    assert result.ok is False
    assert result.error is not None
    assert result.error.get("kind") == "cas_conflict"


def test_running_is_not_failure() -> None:
    assert projection_poll_is_delay(tracker_status="running") is True
    assert projection_poll_is_delay(tracker_status="completed", wait_status="predicate_unmet")
    assert seat_may_fallback_after_projector_failure(
        journal_text="projector_live: d1\n",
        tracker_status="running",
    ) is False


def test_provider_error_is_failure() -> None:
    closeout = "provider_error\nmodel unavailable\n"
    assert closeout_indicates_provider_error(closeout) is True
    assert seat_may_fallback_after_projector_failure(
        journal_text="projector_live: d1\n",
        closeout_text=closeout,
        tracker_status="completed",
    ) is True
    assert writer_mode("projector_live: d1\n") == "projector"


def test_capacity_error_is_failed() -> None:
    err = TrackerCapacityError("full")
    assert isinstance(err, TrackerCapacityError)
    assert seat_may_fallback_after_projector_failure(
        journal_text="projector_live: x\n",
        capacity_error=True,
    ) is True


def test_async_admit_poll_hint_shape() -> None:
    from systems.frontier_consult.handoff_response import build_poll_hint_wait

    hint = build_poll_hint_wait(
        thread_id="12286",
        from_agent="cursor-sdk",
        execution_id="exec-async-1",
    )
    body = {
        "execution_id": "exec-async-1",
        "poll_hint": hint,
        "status": "running",
    }
    assert body["poll_hint"]["tool"] == "wait"
    assert body["poll_hint"]["arguments"]["thread"] == "12286"
    assert body["poll_hint"]["arguments"]["execution_id"] == "exec-async-1"


def test_dispatch_admit_scheduled_only_with_bus_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[str] = []

    def fake_schedule(tracker: Any, record: Any) -> None:
        delivery = record.result_delivery or {}
        scheduled.append(str(delivery.get("bus_thread")))

    monkeypatch.setattr(lifecycle_mod, "_schedule_dispatch_admit", fake_schedule)

    tracker = PipelineExecutionTracker()
    tracker.register_execution(
        execution_id="e-bus",
        pipeline="journal-projector",
        started_at="2026-10-13T00:00:00Z",
        result_delivery={
            "bus_thread": "12286",
            "bus_from_agent": "maestro",
            "bus_to_agent": "cursor-sdk",
        },
    )
    assert scheduled == ["12286"]

    scheduled.clear()
    tracker.register_execution(
        execution_id="e-no-bus",
        pipeline="journal-projector",
        started_at="2026-10-13T00:00:00Z",
        result_delivery={"bus_from_agent": "maestro", "bus_to_agent": "cursor-sdk"},
    )
    assert scheduled == []


@pytest.mark.asyncio
async def test_admit_dispatch_includes_poll_hint_when_bus_thread() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/pipelines/dispatch",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 80),
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    request = Request(scope)
    proxy = MagicMock()
    proxy.is_pipeline_system_ready = True
    proxy.pipeline_registry.is_pipeline.return_value = True
    proxy.pipeline_registry.get_pipeline.return_value = SimpleNamespace(model_extra={})
    proxy.pipeline_executor.generate_execution_id.return_value = "exec-hint"
    proxy.pipeline_executor.execute_async = AsyncMock(return_value=None)
    proxy.pipeline_dispatch_tracker = MagicMock()
    proxy.request_preparer.prepare_request = AsyncMock(
        return_value=SimpleNamespace(options={})
    )

    dispatch = DispatchRequest(
        model="journal-projector",
        messages=[{"role": "user", "content": "TYPE: DISPOSITION\nlesson: x"}],
        result_delivery=ResultDeliveryConfig(
            bus_thread="12286",
            bus_from_agent="maestro",
            bus_to_agent="cursor-sdk",
        ),
    )
    response = await admit_dispatch(request, proxy, dispatch)
    assert response.status_code == 202
    import json as json_mod

    body = json_mod.loads(response.body)
    assert body["poll_hint"]["tool"] == "wait"
    assert body["execution_id"] == "exec-hint"
