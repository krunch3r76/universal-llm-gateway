"""Tests for ImplementSpec v1 schema and implement_spec_hash."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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
    SourceVersion,
    finalize_spec,
    implement_spec_hash,
)


def _ready_spec(**overrides) -> ImplementSpec:  # noqa: ANN003
    base = dict(
        source=Source(
            source_ref="todo:foo",
            canonical_ref="todo:foo",
            source_kind=SourceKind.TODO,
            source_version=SourceVersion(content_hash="sha256:abc"),
        ),
        intent=Intent(summary="test"),
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
    return ImplementSpec(**base)


def test_implement_spec_round_trip() -> None:
    spec = finalize_spec(_ready_spec())
    dumped = spec.model_dump()
    restored = ImplementSpec.model_validate(dumped)
    assert restored.source.source_ref == "todo:foo"


def test_implement_spec_hash_deterministic() -> None:
    spec = finalize_spec(_ready_spec())
    h1 = implement_spec_hash(spec)
    h2 = implement_spec_hash(spec)
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_implement_spec_hash_ignores_freshness_checked_at() -> None:
    from datetime import UTC, datetime

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 6, 8, tzinfo=UTC)
    spec_a = _ready_spec(
        readiness=Readiness(state=ReadinessState.READY, freshness_checked_at=t1),
    )
    spec_b = _ready_spec(
        readiness=Readiness(state=ReadinessState.READY, freshness_checked_at=t2),
    )
    assert implement_spec_hash(spec_a) == implement_spec_hash(spec_b)


def test_implement_spec_hash_excludes_hash_field() -> None:
    spec = _ready_spec()
    h = implement_spec_hash(spec)
    spec_with_hash = spec.model_copy(
        update={"provenance": spec.provenance.model_copy(update={"implement_spec_hash": h})}
    )
    assert implement_spec_hash(spec_with_hash) == h


def test_gated_requires_reason() -> None:
    with pytest.raises(ValidationError):
        Readiness(state=ReadinessState.GATED, gated_reason=None)


def test_gated_forbids_routing() -> None:
    with pytest.raises(ValidationError):
        ImplementSpec(
            source=Source(
                source_ref="agent-bus:1",
                canonical_ref="agent-bus:1",
                source_kind=SourceKind.AGENT_BUS,
            ),
            intent=Intent(summary="x"),
            readiness=Readiness(state=ReadinessState.GATED, gated_reason="ambiguous"),
            routing=Routing(
                orchestration_mode=OrchestrationMode.SINGLE,
                executor_style=ExecutorStyle.REASONING,
                derivation=RoutingDerivation(mode_rule="m", style_rule="s"),
            ),
            acceptance=Acceptance(criteria=["x"]),
            closeout=Closeout(adapter=CloseoutAdapterKind.AGENT_BUS),
        )


def test_ready_requires_routing() -> None:
    with pytest.raises(ValidationError):
        ImplementSpec(
            source=Source(
                source_ref="todo:x",
                canonical_ref="todo:x",
                source_kind=SourceKind.TODO,
            ),
            intent=Intent(summary="x"),
            readiness=Readiness(state=ReadinessState.READY),
            routing=None,
            acceptance=Acceptance(criteria=["x"]),
            closeout=Closeout(adapter=CloseoutAdapterKind.TODO),
        )
