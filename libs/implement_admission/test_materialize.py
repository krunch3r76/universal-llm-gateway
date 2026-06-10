"""Tests for materialize(), packet_sha256 elision, and sufficiency floor (Step 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.admission_read import compute_packet_sha256, read_packet
from implement_admission.materialize import materialize, packet_is_sufficient
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
    Scope,
    Source,
    SourceKind,
    finalize_spec,
    implement_spec_hash,
)

_OLD_SCAFFOLD_MARKER = (
    "Investigate via fs/cortex tools as needed for this implement arc."
)


def _sample_spec(
    *,
    skills: list[str] | None = None,
    criteria: list[str] | None = None,
    files_expected: list[str] | None = None,
    description: str | None = None,
) -> ImplementSpec:
    return finalize_spec(
        ImplementSpec(
            source=Source(
                source_ref="todo:lint-proof",
                canonical_ref="todo:lint-proof",
                source_kind=SourceKind.TODO,
            ),
            intent=Intent(summary="lint proof", description=description),
            scope=Scope(files_expected=files_expected or []),
            readiness=Readiness(state=ReadinessState.READY),
            skills=skills or [],
            routing=Routing(
                orchestration_mode=OrchestrationMode.SINGLE,
                executor_style=ExecutorStyle.MECHANICAL,
                derivation=RoutingDerivation(
                    mode_rule="dispatch_lane=cursor-implement",
                    style_rule="executor_harness=composer-2.5",
                ),
            ),
            acceptance=Acceptance(
                criteria=criteria or ["Criterion one", "Criterion two"]
            ),
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


def test_mcp_capabilities_enriched(tmp_path: Path) -> None:
    spec = _sample_spec(skills=["foo", "bar"], files_expected=["x.py", "y.md"])
    result = materialize(spec, out_dir=tmp_path)
    mcp = result.text.split("<mcp_capabilities>")[1].split("</mcp_capabilities>")[0]
    assert "agent-skills/foo.md" in mcp
    assert "agent-skills/bar.md" in mcp
    assert "quality_gate" in mcp


def test_task_guidance_numbered(tmp_path: Path) -> None:
    result = materialize(_sample_spec(), out_dir=tmp_path)
    guidance = result.text.split("<task_guidance>")[1].split("</task_guidance>")[0]
    assert "1. Criterion one" in guidance
    assert "2. Criterion two" in guidance


def test_acceptance_defaulted_note(tmp_path: Path) -> None:
    spec = _sample_spec(criteria=["Complete lint proof"])
    result = materialize(spec, out_dir=tmp_path)
    guidance = result.text.split("<task_guidance>")[1].split("</task_guidance>")[0]
    assert "acceptance defaulted from source" in guidance


def test_invariants_routing_derivation(tmp_path: Path) -> None:
    spec = _sample_spec(skills=["svc-lifecycle"])
    result = materialize(spec, out_dir=tmp_path)
    inv = result.text.split("<invariants>")[1].split("</invariants>")[0]
    assert "mode_rule:" in inv
    assert "style_rule:" in inv
    assert "Required skills: svc-lifecycle" in inv


def test_sufficiency_enriched_true_skills_empty(tmp_path: Path) -> None:
    result = materialize(_sample_spec(skills=[]), out_dir=tmp_path)
    assert packet_is_sufficient(result.text) is True


def test_sufficiency_enriched_true_skills_present(tmp_path: Path) -> None:
    result = materialize(_sample_spec(skills=["foo"]), out_dir=tmp_path)
    assert packet_is_sufficient(result.text) is True


def test_sufficiency_old_scaffold_false() -> None:
    old = f"""<scope>x</scope>
<invariants>x</invariants>
<task_guidance>## acceptance criteria
- one</task_guidance>
<mcp_capabilities>
{_OLD_SCAFFOLD_MARKER}
</mcp_capabilities>
<output_format>x</output_format>
<corpus>
Source: todo:x
Intent: y
</corpus>
"""
    assert packet_is_sufficient(old) is False


def test_render_does_not_change_spec_hash(tmp_path: Path) -> None:
    spec = _sample_spec(skills=["a"], files_expected=["z.py"])
    before = implement_spec_hash(spec)
    materialize(spec, out_dir=tmp_path)
    after = implement_spec_hash(spec)
    assert before == after


def test_packet_sha256_no_pending(tmp_path: Path) -> None:
    result = materialize(_sample_spec(), out_dir=tmp_path)
    assert "packet_sha256: PENDING" not in result.text
    on_disk = Path(result.path).read_text(encoding="utf-8")
    assert "packet_sha256: PENDING" not in on_disk


def test_packet_sha256_rederivable(tmp_path: Path) -> None:
    ws = tmp_path
    ulg = ws / "universal-llm-gateway" / "tmp" / "mat"
    result = materialize(_sample_spec(), out_dir=ulg)
    rel = f"universal-llm-gateway/tmp/mat/{Path(result.path).name}"
    on_disk = (ws / rel).read_text(encoding="utf-8")
    assert compute_packet_sha256(on_disk) == result.packet_sha256
    read_back = read_packet(rel, workspaces_root=ws)
    assert read_back.packet_sha256 == result.packet_sha256


def test_corpus_includes_description_when_present(tmp_path: Path) -> None:
    result = materialize(
        _sample_spec(description="Detailed intent body"),
        out_dir=tmp_path,
    )
    corpus = result.text.split("<corpus>")[1].split("</corpus>")[0]
    assert "Description: Detailed intent body" in corpus
