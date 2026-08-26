"""Tests for materialize() and validate_packet lint satisfaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from implement_admission.drift_gates import DriftGateState, clear_gate_state_cache
from implement_admission.materialize import materialize
from implement_admission.spec import (
    Acceptance,
    Closeout,
    CloseoutAdapterKind,
    ExecutorStyle,
    ImplementSpec,
    Intent,
    OrchestrationMode,
    Readiness,
    ReadinessState,
    Routing,
    RoutingDerivation,
    Source,
    SourceKind,
    finalize_spec,
)

from .handoff import validate_packet
from .implement_admission_bridge import (
    probe_packet_presence,
    resolve_source_ref_to_packet,
)


class _MaterializeStubCortex:
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        if entity_id == "decision:unified-implement-admission":
            return {
                "id": entity_id,
                "assertions": [
                    {
                        "confidence": "confirmed",
                        "superseded": False,
                        "superseded_by": None,
                    },
                ],
            }
        return {
            "id": entity_id,
            "name": entity_id,
            "attributes": {
                "content_hash": "sha256:fixture",
                "acceptance_criteria": ["AC1", "AC2"],
                "files_expected": ["a.py"],
            },
        }


def _patch_gates_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "implement_admission.drift_gates.gate_state",
        lambda gate_id: DriftGateState.WARN,
    )
    clear_gate_state_cache()


def _sample_spec() -> ImplementSpec:
    return finalize_spec(
        ImplementSpec(
            source=Source(
                source_ref="todo:lint-proof",
                canonical_ref="todo:lint-proof",
                source_kind=SourceKind.TODO,
            ),
            intent=Intent(summary="lint proof"),
            readiness=Readiness(state=ReadinessState.READY),
            routing=Routing(
                orchestration_mode=OrchestrationMode.SINGLE,
                executor_style=ExecutorStyle.MECHANICAL,
                derivation=RoutingDerivation(mode_rule="m", style_rule="s"),
            ),
            acceptance=Acceptance(criteria=["Criterion one", "Criterion two"]),
            closeout=Closeout(adapter=CloseoutAdapterKind.TODO),
        )
    )


def test_materialized_packet_has_six_blocks(tmp_path: Path) -> None:
    result = materialize(_sample_spec(), out_dir=tmp_path)
    text = result.text
    for tag in (
        "<scope>",
        "<invariants>",
        "<task_guidance>",
        "<mcp_capabilities>",
        "<output_format>",
        "<corpus>",
    ):
        assert tag in text
    assert "acceptance" in text.lower()


def test_materialized_packet_admitted_by_validate_packet(tmp_path: Path) -> None:
    ws_root = tmp_path
    ulg = ws_root / "universal-llm-gateway" / "tmp" / "materialized"
    result = materialize(_sample_spec(), out_dir=ulg)
    rel = "universal-llm-gateway/tmp/materialized/" + Path(result.path).name
    validate_packet(
        request_id="test-req",
        packet_path=rel,
        to_agent="claude-cursor",
        handoff_contract="implement",
        workspaces_root=ws_root,
    )


def test_probe_packet_presence_true_when_file_exists(tmp_path: Path) -> None:
    packet = tmp_path / "universal-llm-gateway" / "tmp" / "pkt.md"
    packet.parent.mkdir(parents=True)
    packet.write_text("---\n---\n", encoding="utf-8")
    rel = "universal-llm-gateway/tmp/pkt.md"
    assert probe_packet_presence(rel, workspaces_root=tmp_path) is True


def test_probe_packet_presence_false_cross_mount(tmp_path: Path) -> None:
    write_root = tmp_path / "stargate"
    executor_root = tmp_path / "executor"
    write_root.mkdir()
    executor_root.mkdir()
    packet = write_root / "universal-llm-gateway" / "tmp" / "pkt.md"
    packet.parent.mkdir(parents=True)
    packet.write_text("---\n---\n", encoding="utf-8")
    rel = "universal-llm-gateway/tmp/pkt.md"
    assert (
        probe_packet_presence(rel, workspaces_root=write_root, probe_root=executor_root)
        is False
    )


def test_materialize_lane_shared_mount_no_probe_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_gates_warn(monkeypatch)
    monkeypatch.delenv("HANDOFF_EXECUTOR_WORKSPACES_ROOT", raising=False)
    result = resolve_source_ref_to_packet(
        "todo:lint-proof",
        cortex=_MaterializeStubCortex(),
        workspaces_root=tmp_path,
        request_id="req-shared",
    )
    assert result.materialization_present is True
    assert result.warnings == []


def test_materialize_lane_cross_mount_sets_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_gates_warn(monkeypatch)
    write_root = tmp_path / "stargate"
    executor_root = tmp_path / "executor"
    write_root.mkdir()
    executor_root.mkdir()
    monkeypatch.setenv("HANDOFF_EXECUTOR_WORKSPACES_ROOT", str(executor_root))
    result = resolve_source_ref_to_packet(
        "todo:lint-proof",
        cortex=_MaterializeStubCortex(),
        workspaces_root=write_root,
        request_id="req-cross",
    )
    assert result.materialization_present is False
    assert len(result.warnings) == 1
    assert "materialization.executor_absent" in result.warnings[0]
    assert "use source_ref fallback" in result.warnings[0]


def test_conductor_resolve_passes_fold_deps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class _Packet:
        path = str(
            tmp_path
            / "universal-llm-gateway"
            / "tmp"
            / "implement-admission"
            / "materialized"
            / "conductor-fold-probe.md"
        )
        packet_sha256 = "deadbeef"
        text = ""

    def _materialize(source_ref: str, **kwargs: Any) -> _Packet:
        captured["source_ref"] = source_ref
        captured.update(kwargs)
        Path(_Packet.path).parent.mkdir(parents=True, exist_ok=True)
        Path(_Packet.path).write_text("ok\n", encoding="utf-8")
        return _Packet()

    monkeypatch.setattr(
        "systems.frontier_consult.implement_admission_bridge.materialize_conductor",
        _materialize,
    )
    result = resolve_source_ref_to_packet(
        "todo:fold-probe-slug",
        cortex=_MaterializeStubCortex(),
        workspaces_root=tmp_path,
        packet_kind="conductor",
        summoning_thread_id="9638",
    )
    assert result.gated is False
    assert captured["fold_deps"] is not None
    assert captured["fold_deps"].source_ref == "todo:fold-probe-slug"
    assert captured["fold_deps"].summoning_thread_id == "9638"
    assert captured["summoning_thread_id"] == "9638"
