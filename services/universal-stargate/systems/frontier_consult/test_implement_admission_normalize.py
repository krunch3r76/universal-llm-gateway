"""Tests for source_ref grammar and normalize()."""

from __future__ import annotations

import pytest

from implement_admission.normalize import normalize
from implement_admission.source_ref import SourceRefError, parse_source_ref
from implement_admission.spec import ReadinessState, SourceKind


class _StubCortex:
    def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        attrs: dict = {
            "content_hash": "sha256:fixture",
            "acceptance_criteria": ["AC1", "AC2"],
        }
        if entity_id.startswith("plan:"):
            attrs["phases"] = ["phase-1", "phase-2"]
        if entity_id.startswith("plan_phase:"):
            attrs["phase_number"] = 2
        if "threshold" in entity_id:
            attrs["multi_phase_arc"] = True
        if "bounded" in entity_id:
            attrs["files_expected"] = ["a.py", "b.py"]
        return {"id": entity_id, "name": entity_id, "attributes": attrs}


def test_parse_todo() -> None:
    ref = parse_source_ref("todo:relay-edge")
    assert ref.source_kind == SourceKind.TODO.value
    assert ref.canonical_ref == "todo:relay-edge"


def test_parse_plan_phase_shorthand() -> None:
    ref = parse_source_ref("plan:arc/phase-3")
    assert ref.source_kind == SourceKind.PLAN_PHASE.value
    assert ref.canonical_ref == "plan_phase:arc/phase-3"
    assert ref.parent_ref == "plan:arc"
    assert ref.selector == "phase-3"


def test_parse_agent_bus_with_turn() -> None:
    ref = parse_source_ref("agent-bus:1351#turn-2")
    assert ref.turn == 2
    assert ref.canonical_ref == "agent-bus:1351#turn-2"


def test_parse_packet_uri() -> None:
    ref = parse_source_ref("packet:ws://universal-llm-gateway/tmp/foo.md")
    assert ref.source_kind == SourceKind.PACKET.value


def test_unparseable_raises() -> None:
    with pytest.raises(SourceRefError) as exc:
        parse_source_ref("not-a-ref")
    assert exc.value.code == "source_ref_unparseable"


def test_ambiguous_agent_bus_gated() -> None:
    spec = normalize("agent-bus:1351", cortex=_StubCortex())
    assert spec.readiness.state == ReadinessState.GATED
    assert spec.routing is None


def test_entity_not_found() -> None:
    class _Missing:
        def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
            return {}

    with pytest.raises(SourceRefError) as exc:
        normalize("todo:missing", cortex=_Missing())
    assert exc.value.code == "source_not_found"


# ---------------------------------------------------------------------------
# Friction 20663 regression: task: source_ref must not 422 on packet_path lane
# ---------------------------------------------------------------------------


def test_parse_task_ref() -> None:
    """task: is now a recognised source_ref kind (provenance-only, not dispatchable)."""
    ref = parse_source_ref("task:my-feature-spec")
    assert ref.source_kind == SourceKind.TASK.value
    assert ref.canonical_ref == "task:my-feature-spec"
    assert ref.parent_ref is None
    assert ref.turn is None


def test_require_implement_ready_noops_for_task_ref() -> None:
    """require_implement_ready must not raise for task: source refs.

    The gate calls parse_source_ref then checks source_kind != TODO → early return.
    With TASK registered in SourceKind, the early-exit is reached before any cortex calls.
    """
    from systems.frontier_consult.implement_ready_gate import require_implement_ready

    # Must complete without raising — no cortex methods are called
    require_implement_ready(
        request_id="test-fr20663",
        source_ref="task:my-feature-spec",
        cortex=_StubCortex(),  # type: ignore[arg-type]
    )


def test_normalize_task_ref_unsupported() -> None:
    """normalize() must raise source_ref_not_dispatchable for task: refs.

    task: is provenance-only; it is not a materialisable dispatch source.
    The guard inserted in normalize() fires before _normalize_entity is reached.
    """
    from implement_admission.source_ref import SourceRefError

    with pytest.raises(SourceRefError) as exc:
        normalize("task:my-feature-spec", cortex=_StubCortex())
    assert exc.value.code == "source_ref_not_dispatchable"
