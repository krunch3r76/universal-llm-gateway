"""Tests for classify_risk_tier and author_family threading (Phase-1 attestation)."""

from __future__ import annotations

import pytest

from implement_admission.normalize import normalize
from implement_admission.routing import (
    classify_risk_tier,
    normalize_author_family,
    resolve_route_contract,
)
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
)


def _ready_spec(**overrides) -> ImplementSpec:  # noqa: ANN003
    base = dict(
        source=Source(
            source_ref="todo:foo",
            canonical_ref="todo:foo",
            source_kind=SourceKind.TODO,
        ),
        intent=Intent(summary="rename a local var"),
        scope=Scope(files_expected=[]),
        readiness=Readiness(state=ReadinessState.READY),
        routing=Routing(
            orchestration_mode=OrchestrationMode.SINGLE,
            executor_style=ExecutorStyle.MECHANICAL,
            derivation=RoutingDerivation(mode_rule="m", style_rule="s"),
        ),
        acceptance=Acceptance(criteria=["done"]),
        closeout=Closeout(adapter=CloseoutAdapterKind.TODO),
    )
    base.update(overrides)
    return finalize_spec(ImplementSpec(**base))


@pytest.mark.parametrize(
    ("summary", "files", "expected"),
    [
        ("drop table on prod", [], "critical"),
        ("schema migration for users", [], "material"),
        ("rename a local var", ["libs/cortex_store/routes/x.py"], "material"),
        ("rename a local var", ["migrations/001_init.py"], "material"),
        ("rename a local var", [], "mechanical"),
    ],
)
def test_classify_risk_tier_precedence(
    summary: str,
    files: list[str],
    expected: str,
) -> None:
    spec = _ready_spec(
        intent=Intent(summary=summary),
        scope=Scope(files_expected=files),
    )
    assert classify_risk_tier(spec) == expected


def test_required_covers_critical_and_material_for_claude() -> None:
    class Reader:
        def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003
            return {
                "id": entity_id,
                "name": "task",
                "attributes": {
                    "acceptance_criteria": ["Complete migration rollout"],
                },
            }

    critical = normalize(
        "todo:critical",
        cortex=Reader(),
        author_family="claude-cursor",
    )
    critical = critical.model_copy(
        update={"intent": Intent(summary="irreversible prod change")}
    )
    critical_att = ReviewAttestation(
        required=(
            classify_risk_tier(critical) in {"material", "critical"}
            and normalize_author_family("claude-cursor") == "claude"
        ),
        risk_tier=classify_risk_tier(critical),
        author_family="claude",
        disposition="missing",
    )
    assert classify_risk_tier(critical) == "critical"
    assert critical_att.required is True

    material = normalize("todo:mat", cortex=Reader(), author_family="claude-cursor")
    assert material.provenance.review_attestation.required is True
    assert material.provenance.review_attestation.risk_tier == "material"

    mechanical = normalize(
        "todo:mech",
        cortex=_StubCortex(),
        author_family="claude-cursor",
    )
    mechanical = mechanical.model_copy(
        update={"intent": Intent(summary="rename a local var")}
    )
    mech_att = ReviewAttestation(
        required=(
            classify_risk_tier(mechanical) in {"material", "critical"}
            and normalize_author_family("claude-cursor") == "claude"
        ),
        risk_tier=classify_risk_tier(mechanical),
        author_family="claude",
        disposition="missing",
    )
    assert classify_risk_tier(mechanical) == "mechanical"
    assert mech_att.required is False


@pytest.mark.parametrize(
    ("author", "expected_required"),
    [
        ("claude-cursor", True),
        ("claude-web", True),
        ("gpt-cursor", False),
        ("grok-cursor", False),
        (None, True),
    ],
)
def test_author_family_threading_material(
    author: str | None,
    expected_required: bool,
) -> None:
    class Reader:
        def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003
            return {
                "id": entity_id,
                "name": "dispatch infra",
                "attributes": {
                    "acceptance_criteria": ["wire admission handoff"],
                },
            }

    spec = normalize(
        "todo:foo",
        cortex=Reader(),
        author_family=author,
    )
    att = spec.provenance.review_attestation
    assert att is not None
    assert att.risk_tier == "material"
    assert att.required is expected_required


def test_normalize_author_family_conservative_unknown() -> None:
    assert normalize_author_family(None) == "claude"
    assert normalize_author_family("unknown-seat") == "claude"
    assert normalize_author_family("gpt-cursor") == "gpt"


def test_handoff_implement_requires_manual_pickup() -> None:
    spec = _ready_spec()
    rc = resolve_route_contract(
        spec,
        spec.routing,
        classify_risk_tier(spec),
        contract="implement",
        role="cursor-implement",
    )
    assert rc.autonomy == "manual_pickup"
    assert rc.operator_pickup_required is True


class _StubCortex:
    def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003
        return {
            "id": entity_id,
            "name": "x",
            "attributes": {"acceptance_criteria": ["done"]},
        }
