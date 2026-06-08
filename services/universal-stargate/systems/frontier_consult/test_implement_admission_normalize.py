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
            attrs["trips_todo_plan_threshold"] = True
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
