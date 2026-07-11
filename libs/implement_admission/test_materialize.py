"""Tests for materialize(), packet_sha256 elision, and sufficiency floor (Step 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.admission_read import (
    compute_packet_sha256,
    read_packet,
    replace_frontmatter_value,
)
from implement_admission.materialize import (
    _is_defaulted_acceptance,
    _render_corpus,
    materialize,
    packet_is_sufficient,
)
from implement_admission.skill_source_table import SkillSourceResolveError
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
    ReviewAttestation,
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
    spec = _sample_spec(
        skills=["git-posture", "architecture-invariants"],
        files_expected=["x.py", "y.md"],
    )
    result = materialize(spec, out_dir=tmp_path)
    mcp = result.text.split("<mcp_capabilities>")[1].split("</mcp_capabilities>")[0]
    assert "Use the `git-posture` skill" in mcp
    assert "Use the `architecture-invariants` skill" in mcp
    assert "quality_gate" in mcp
    assert "agent-skills/" not in mcp


def test_materialize_fail_loud_unknown_skill(tmp_path: Path) -> None:
    spec = _sample_spec(skills=["foo"])
    with pytest.raises(SkillSourceResolveError):
        materialize(spec, out_dir=tmp_path)


def test_mcp_capabilities_always_carries_arch_skillrefs(tmp_path: Path) -> None:
    """Materialized packets always reference the universal + ULG arch layers so a
    handoff to an MCP seat passes handoff_packet_missing_arch_skillrefs, even when
    the source entity's required_skills omitted them."""
    mcp = (
        materialize(_sample_spec(skills=[]), out_dir=tmp_path)
        .text.split("<mcp_capabilities>")[1]
        .split("</mcp_capabilities>")[0]
    )
    assert "Use the `architecture-invariants` skill" in mcp
    assert "Use the `ulg-architecture` skill" in mcp
    assert "agent-skills/" not in mcp


def test_mcp_capabilities_arch_skillrefs_not_duplicated(tmp_path: Path) -> None:
    """A required_skill that overlaps the arch set is emitted once."""
    mcp = (
        materialize(_sample_spec(skills=["ulg-architecture"]), out_dir=tmp_path)
        .text.split("<mcp_capabilities>")[1]
        .split("</mcp_capabilities>")[0]
    )
    assert mcp.count("Use the `ulg-architecture` skill") == 1


def test_mcp_capabilities_advertises_coding_session_bundle(tmp_path: Path) -> None:
    mcp = (
        materialize(_sample_spec(skills=[]), out_dir=tmp_path)
        .text.split("<mcp_capabilities>")[1]
        .split("</mcp_capabilities>")[0]
    )
    assert "Use the `git-posture` skill" in mcp
    assert "Use the `service-lifecycle` skill" in mcp
    for slug in (
        "implement-work-item",
        "completion-provenance-discipline",
        "fs",
        "service-lifecycle",
    ):
        assert f"Use the `{slug}` skill" in mcp
    assert "agent-skills/" not in mcp


def test_mcp_capabilities_advertise_tier_deduped_against_spec_skills(
    tmp_path: Path,
) -> None:
    mcp = (
        materialize(_sample_spec(skills=["git-posture"]), out_dir=tmp_path)
        .text.split("<mcp_capabilities>")[1]
        .split("</mcp_capabilities>")[0]
    )
    assert mcp.count("Use the `git-posture` skill") == 1


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
    spec = _sample_spec(skills=["service-lifecycle"])
    result = materialize(spec, out_dir=tmp_path)
    inv = result.text.split("<invariants>")[1].split("</invariants>")[0]
    assert "mode_rule:" in inv
    assert "style_rule:" in inv
    assert "Required skills: service-lifecycle" in inv


def test_sufficiency_enriched_true_skills_empty(tmp_path: Path) -> None:
    result = materialize(_sample_spec(skills=[]), out_dir=tmp_path)
    assert packet_is_sufficient(result.text) is True


def test_sufficiency_enriched_true_skills_present(tmp_path: Path) -> None:
    result = materialize(_sample_spec(skills=["git-posture"]), out_dir=tmp_path)
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
    spec = _sample_spec(skills=["git-posture"], files_expected=["z.py"])
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


def test_corpus_todo_includes_authoritative_attrs_line(tmp_path: Path) -> None:
    result = materialize(_sample_spec(), out_dir=tmp_path)
    corpus = result.text.split("<corpus>")[1].split("</corpus>")[0]
    assert "attributes are authoritative" in corpus


def test_corpus_todo_includes_narrative_spec_when_source_uri(tmp_path: Path) -> None:
    spec = _sample_spec()
    spec = spec.model_copy(
        update={
            "source": spec.source.model_copy(
                update={"source_uri": "tasks/specs/implement-input-schema.md"}
            )
        }
    )
    result = materialize(spec, out_dir=tmp_path)
    corpus = result.text.split("<corpus>")[1].split("</corpus>")[0]
    assert (
        "narrative spec: tasks/specs/implement-input-schema.md; "
        "attributes are authoritative"
    ) in corpus


def test_source_uri_elided_from_spec_hash(tmp_path: Path) -> None:
    base = _sample_spec()
    with_uri = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"source_uri": "tasks/specs/foo.md"}
            )
        }
    )
    assert implement_spec_hash(base) == implement_spec_hash(with_uri)
    materialize(with_uri, out_dir=tmp_path)


def _spec_with_attestation(**att_overrides) -> ImplementSpec:
    spec = _sample_spec()
    att = ReviewAttestation(**att_overrides)
    return spec.model_copy(
        update={
            "provenance": spec.provenance.model_copy(update={"review_attestation": att})
        }
    )


def test_packet_sha256_elides_review_attestation_block(tmp_path: Path) -> None:
    plain = materialize(_sample_spec(), out_dir=tmp_path)
    stamped = materialize(
        _spec_with_attestation(risk_tier="material"), out_dir=tmp_path
    )
    assert compute_packet_sha256(plain.text) == compute_packet_sha256(stamped.text)

    changed = replace_frontmatter_value(plain.text, "source_ref", "todo:changed")
    assert compute_packet_sha256(changed) != compute_packet_sha256(plain.text)


def test_review_attestation_block_emitted_sorted(tmp_path: Path) -> None:
    result = materialize(
        _spec_with_attestation(
            risk_tier="material",
            required=True,
            disposition="missing",
            unresolved_blocker_ids=["z", "a"],
        ),
        out_dir=tmp_path,
    )
    fm = result.text.split("---")[1]
    assert "review_attestation:" in fm
    assert "  author_family: claude" in fm
    assert "  disposition: missing" in fm
    assert "  required: true" in fm
    assert "  risk_tier: material" in fm
    assert "  spec_hash: unbound" in fm
    assert "unresolved_blocker_ids:" in fm


def test_packet_sha256_deterministic_across_repeated_materialize(
    tmp_path: Path,
) -> None:
    spec = _spec_with_attestation(risk_tier="material", required=True)
    first = materialize(spec, out_dir=tmp_path / "a")
    second = materialize(spec, out_dir=tmp_path / "b")
    assert first.packet_sha256 == second.packet_sha256


def test_todo_corpus_headers_only_no_spec_prose_body() -> None:
    spec_path = Path(__file__).resolve().parents[2] / (
        "tasks/specs/densify-spec-attribute-distillation.md"
    )
    spec_prose_snippet = "The two-schema distinction (the whole bug)."
    assert spec_prose_snippet in spec_path.read_text(encoding="utf-8")
    spec = _sample_spec(
        criteria=["Real AC one", "Real AC two"],
        files_expected=["libs/a.py"],
    ).model_copy(
        update={
            "source": _sample_spec().source.model_copy(
                update={"source_uri": str(spec_path.relative_to(spec_path.parents[2]))},
            ),
        }
    )
    corpus = _render_corpus(spec)
    assert "Source:" in corpus
    assert "Intent:" in corpus
    assert "attributes are authoritative" in corpus
    assert spec_prose_snippet not in corpus


def test_full_packet_with_distilled_attrs_not_defaulted(tmp_path: Path) -> None:
    criteria = [
        "AC one",
        "AC two",
        "AC three",
        "AC four",
        "AC five",
        "AC six",
        "AC seven",
    ]
    files = [f"libs/file{i}.py" for i in range(7)]
    spec = _sample_spec(criteria=criteria, files_expected=files)
    assert _is_defaulted_acceptance(spec) is False
    result = materialize(spec, out_dir=tmp_path)
    corpus = result.text.split("<corpus>")[1].split("</corpus>")[0]
    assert "Acceptance criteria count: 7" in corpus
    assert "Files expected:" in corpus
    assert "acceptance defaulted from source" not in result.text
    assert packet_is_sufficient(result.text) is True
