"""Tests for materialize() and validate_packet lint satisfaction."""

from __future__ import annotations

from pathlib import Path

from implement_admission.materialize import materialize
from implement_admission.spec import (
    Acceptance,
    Closeout,
    CloseoutAdapterKind,
    ImplementSpec,
    Intent,
    OrchestrationMode,
    ExecutorStyle,
    Readiness,
    ReadinessState,
    Routing,
    RoutingDerivation,
    Source,
    SourceKind,
    finalize_spec,
)

from .handoff import validate_packet


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
