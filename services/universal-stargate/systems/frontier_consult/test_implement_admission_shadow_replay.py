"""Tests for shadow replay falsifier harness."""

from __future__ import annotations

import json
from pathlib import Path

from implement_admission.admission_read import read_packet

from .shadow_replay import ReplayCase, classify, run_replay


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "implement_admission"


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
        if "bounded" in entity_id or "relay-bounded" in entity_id:
            attrs["files_expected"] = ["a.py", "b.py"]
        if entity_id.startswith("plan:"):
            attrs["open_design"] = True
        return {"id": entity_id, "name": entity_id, "attributes": attrs}


def _load_fixtures() -> list[ReplayCase]:
    cases: list[ReplayCase] = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        cases.append(
            ReplayCase(
                source_ref=item["source_ref"],
                legacy_route=item.get("legacy_route") or {},
                legacy_closeout_mutation=item.get("legacy_closeout_mutation"),
                door=item.get("door", "fixture"),
            )
        )
    return cases


def test_friction_rate_math() -> None:
    cases = [
        ReplayCase("agent-bus:1", {"gated": True}, None, "bus"),
        ReplayCase("agent-bus:2", {"gated": False}, None, "bus"),
    ]

    class _BusCortex:
        def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
            return {"id": entity_id, "attributes": {}}

    report = run_replay(cases, cortex=_BusCortex(), min_n=2, threshold=0.10)
    assert report.n == 2
    assert 0.0 <= report.friction_rate <= 1.0


def test_passed_false_when_n_below_min_n() -> None:
    report = run_replay([], cortex=_StubCortex(), min_n=150, threshold=0.10)
    assert report.passed is False
    assert report.n == 0


def test_passed_false_when_friction_above_threshold() -> None:
    cases = [
        ReplayCase("agent-bus:9", {"gated": False}, None, "bus"),
    ]
    report = run_replay(cases, cortex=_StubCortex(), min_n=1, threshold=0.0)
    assert report.passed is False


def test_golden_fixtures_classify_match() -> None:
    ws_root = Path("/mnt/torus/projects")
    cases = _load_fixtures()
    assert len(cases) >= 6
    for case in cases:
        label = classify(case, cortex=_StubCortex(), workspaces_root=ws_root)
        assert label == "match", f"{case.source_ref} got {label}"


def test_admission_read_canonical_api() -> None:
    good = FIXTURE_DIR / "packets" / "good-with-acceptance.md"
    rel = (
        "universal-llm-gateway/services/universal-stargate/systems/frontier_consult/"
        f"fixtures/implement_admission/packets/{good.name}"
    )
    packet = read_packet(rel, workspaces_root=Path("/mnt/torus/projects"))
    assert "acceptance" in packet.text.lower()
    assert packet.packet_sha256.startswith("sha256:")
