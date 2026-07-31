"""Offline unit tests for generate_wrap.prepare_implement_packet."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from implement_admission.preflight import DecisionNotAssertedError

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.generate_wrap import (
    GenerateWrapResult,
    prepare_implement_packet,
)
from systems.frontier_consult.implement_admission_bridge import BridgeResult


@pytest.mark.offline
def test_inline_packet_passthrough_skips_materialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Caller packet_path is returned unchanged; bridge never invoked."""
    gate_calls: list[str | None] = []
    bridge_called = False

    def _gate(**kwargs: object) -> None:
        gate_calls.append(kwargs.get("source_ref"))  # type: ignore[arg-type]

    def _bridge(*args: object, **kwargs: object) -> BridgeResult:
        nonlocal bridge_called
        bridge_called = True
        return BridgeResult(gated=False, packet_path="should-not-run")

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_implement_ready", _gate
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_source_ref_to_packet", _bridge
    )

    result = prepare_implement_packet(
        request_id="req-1",
        source_ref="todo:some-slug",
        packet_path="tmp/reviews/packet.md",
        caller_agent=None,
        cortex=MagicMock(),
        workspaces_root=tmp_path,
    )

    assert result == GenerateWrapResult(packet_path="tmp/reviews/packet.md")
    assert gate_calls == ["todo:some-slug"]
    assert bridge_called is False


@pytest.mark.offline
def test_materialization_sub_path_gate_then_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bare source_ref: gate and bridge both receive the same source_ref (Fork H)."""
    gate_refs: list[str | None] = []
    bridge_refs: list[str] = []
    decision_called = False

    def _gate(**kwargs: object) -> None:
        gate_refs.append(kwargs.get("source_ref"))  # type: ignore[arg-type]

    def _decision(**kwargs: object) -> None:  # noqa: ARG001
        nonlocal decision_called
        decision_called = True

    def _bridge(source_ref: str, **kwargs: object) -> BridgeResult:
        bridge_refs.append(source_ref)
        return BridgeResult(
            gated=False,
            packet_path="tmp/reviews/materialized.md",
            warnings=["executor-absent"],
        )

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_implement_ready", _gate
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_decision_asserted", _decision
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_source_ref_to_packet", _bridge
    )

    result = prepare_implement_packet(
        request_id="req-2",
        source_ref="todo:first-class-wrap-transport",
        packet_path=None,
        caller_agent="cursor",
        cortex=MagicMock(),
        workspaces_root=tmp_path,
    )

    assert result.packet_path == "tmp/reviews/materialized.md"
    assert result.materialized is True
    assert result.warnings == ["executor-absent"]
    assert gate_refs == ["todo:first-class-wrap-transport"]
    assert bridge_refs == ["todo:first-class-wrap-transport"]
    assert decision_called is True


@pytest.mark.offline
def test_bridge_gated_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bridge gated result propagates without a packet path."""
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_implement_ready",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_decision_asserted",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_source_ref_to_packet",
        lambda *args, **kwargs: BridgeResult(
            gated=True,
            gated_reason="judgment_required",
        ),
    )

    result = prepare_implement_packet(
        request_id="req-3",
        source_ref="todo:gated-slug",
        packet_path=None,
        caller_agent=None,
        cortex=MagicMock(),
        workspaces_root=tmp_path,
    )

    assert result.gated is True
    assert result.gated_reason == "judgment_required"
    assert result.packet_path is None


@pytest.mark.offline
def test_gate_failure_prevents_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """require_implement_ready failure prevents materialization (Fork A)."""
    bridge_called = False

    def _gate(**kwargs: object) -> None:  # noqa: ARG001
        raise FrontierEndpointError(
            request_id="req-4",
            field="source_ref",
            reason="dense spec invalid",
            status_code=422,
            code="dense_spec_invalid",
        )

    def _bridge(*args: object, **kwargs: object) -> BridgeResult:
        nonlocal bridge_called
        bridge_called = True
        return BridgeResult(gated=False, packet_path="x")

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_implement_ready", _gate
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_source_ref_to_packet", _bridge
    )

    with pytest.raises(FrontierEndpointError) as exc_info:
        prepare_implement_packet(
            request_id="req-4",
            source_ref="todo:bad-spec",
            packet_path=None,
            caller_agent=None,
            cortex=MagicMock(),
            workspaces_root=tmp_path,
        )

    assert exc_info.value.code == "dense_spec_invalid"
    assert bridge_called is False


@pytest.mark.offline
def test_decision_not_asserted_blocks_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Materialization sub-path enforces require_decision_asserted (Fork E / AC-3a)."""
    bridge_called = False

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_implement_ready",
        lambda **kwargs: None,
    )

    def _fail_decision(**kwargs: object) -> None:  # noqa: ARG001
        raise DecisionNotAssertedError()

    def _bridge(*args: object, **kwargs: object) -> BridgeResult:
        nonlocal bridge_called
        bridge_called = True
        return BridgeResult(gated=False, packet_path="x")

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_decision_asserted",
        _fail_decision,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_source_ref_to_packet", _bridge
    )

    with pytest.raises(DecisionNotAssertedError):
        prepare_implement_packet(
            request_id="req-5",
            source_ref="todo:unratified",
            packet_path=None,
            caller_agent=None,
            cortex=MagicMock(),
            workspaces_root=tmp_path,
        )

    assert bridge_called is False


@pytest.mark.offline
def test_packet_path_only_skips_decision_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Caller packet_path does not invoke require_decision_asserted."""
    decision_called = False

    def _track_decision(**kwargs: object) -> None:  # noqa: ARG001
        nonlocal decision_called
        decision_called = True

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_implement_ready",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.require_decision_asserted",
        _track_decision,
    )

    result = prepare_implement_packet(
        request_id="req-6",
        source_ref=None,
        packet_path="tmp/reviews/inline.md",
        caller_agent=None,
        cortex=MagicMock(),
        workspaces_root=tmp_path,
    )

    assert result.packet_path == "tmp/reviews/inline.md"
    assert decision_called is False


@pytest.mark.offline
def test_prepare_implement_packet_noops_for_task_front_matter_source_ref(
    tmp_path: Path,
) -> None:
    """packet_path lane: task: front-matter source_ref must not raise (fr20663).

    Before the fix: prepare_implement_packet read the front-matter source_ref and passed
    it to require_implement_ready, which called parse_source_ref("task:...") → 422.
    After the fix: parse succeeds → gate non-todo early-exit → return (no 422).
    """
    packet_path = "tmp/reviews/my-task-spec.md"
    packet_file = tmp_path / packet_path
    packet_file.parent.mkdir(parents=True, exist_ok=True)
    packet_file.write_text(
        "---\nsource_ref: task:my-feature-slug\n---\n\n# Spec\n\nSome content.\n",
        encoding="utf-8",
    )

    result = prepare_implement_packet(
        request_id="test-fr20663-integration",
        source_ref=None,
        packet_path=packet_path,
        caller_agent=None,
        cortex=MagicMock(),
        workspaces_root=tmp_path,
    )

    assert result is not None
    assert result.packet_path == packet_path
    assert result.materialized is False
    assert result.gated is False
