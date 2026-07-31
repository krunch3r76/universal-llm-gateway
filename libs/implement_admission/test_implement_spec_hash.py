"""Property tests for implement_spec_hash encoder fail-closed behavior and parity."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, get_args, get_origin

import pytest
from pydantic import BaseModel

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
    SourceVersion,
    _canonical_default,
    finalize_spec,
    implement_spec_hash,
)

GOLDEN_READY_ROUTING = (
    "sha256:71957ced9dd4ef6259254c6847f62f0a3ff8614732effa56113b35f33718bc23"
)
GOLDEN_GATED_NO_ROUTING = (
    "sha256:931939b1d16d6133608d590197d78a06fbb3c7a92b5150a25b61d7e7112f5a2a"
)
GOLDEN_READY_WITH_ATTESTATION = (
    "sha256:71957ced9dd4ef6259254c6847f62f0a3ff8614732effa56113b35f33718bc23"
)

ELIDED_PATHS = frozenset(
    {
        "provenance.implement_spec_hash",
        "provenance.review_attestation",
        "provenance.created_at",
        "readiness.freshness_checked_at",
        "scope.deck_body",
        "source.source_uri",
        "source.source_version.deck_sha256",
    }
)

PARTICIPATING_PATHS = frozenset(
    {
        "schema_version",
        "source.source_ref",
        "source.canonical_ref",
        "source.parent_ref",
        "source.selector",
        "source.source_kind",
        "source.source_version.content_hash",
        "source.source_version.packet_sha256",
        "intent.summary",
        "intent.description",
        "scope.files_expected",
        "scope.bounded",
        "readiness.state",
        "readiness.gated_reason",
        "skills",
        "routing.orchestration_mode",
        "routing.executor_style",
        "routing.checkpoint_required",
        "routing.derivation.mode_rule",
        "routing.derivation.style_rule",
        "routing.requested_execution_mode",
        "acceptance.criteria",
        "closeout.adapter",
        "closeout.bus_thread",
        "provenance.generated_from",
        "provenance.normalizer_version",
    }
)


def _ready_spec(**overrides: Any) -> ImplementSpec:
    base: dict[str, Any] = dict(
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


def _gated_spec() -> ImplementSpec:
    return ImplementSpec(
        source=Source(
            source_ref="agent-bus:1",
            canonical_ref="agent-bus:1",
            source_kind=SourceKind.AGENT_BUS,
        ),
        intent=Intent(summary="gated task"),
        readiness=Readiness(state=ReadinessState.GATED, gated_reason="ambiguous"),
        acceptance=Acceptance(criteria=["x"]),
        closeout=Closeout(adapter=CloseoutAdapterKind.AGENT_BUS),
    )


def _ready_with_attestation() -> ImplementSpec:
    spec = finalize_spec(_ready_spec())
    h0 = implement_spec_hash(spec)
    att = ReviewAttestation(
        required=True,
        risk_tier="material",
        disposition="pass",
        spec_hash=h0,
        reviewer_family="gpt",
    )
    return spec.model_copy(
        update={
            "provenance": spec.provenance.model_copy(update={"review_attestation": att})
        }
    )


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = get_args(annotation)
    non_none = [arg for arg in args if arg is not type(None)]
    if len(non_none) == 1:
        return _unwrap_optional(non_none[0])
    return annotation


def _enumerate_leaf_paths(
    model: type[BaseModel],
    *,
    prefix: str = "",
    elided_prefixes: frozenset[str] = ELIDED_PATHS,
) -> set[str]:
    paths: set[str] = set()
    for name, field in model.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        if path in elided_prefixes:
            paths.add(path)
            continue
        annotation = _unwrap_optional(field.annotation)
        origin = get_origin(annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths |= _enumerate_leaf_paths(
                annotation, prefix=path, elided_prefixes=elided_prefixes
            )
        elif origin is list:
            paths.add(path)
        elif origin is dict:
            paths.add(path)
        else:
            paths.add(path)
    return paths


# T1 — golden parity
@pytest.mark.parametrize(
    ("fixture_fn", "golden"),
    [
        (_ready_spec, GOLDEN_READY_ROUTING),
        (_gated_spec, GOLDEN_GATED_NO_ROUTING),
        (_ready_with_attestation, GOLDEN_READY_WITH_ATTESTATION),
    ],
    ids=["ready_routing", "gated_no_routing", "ready_with_attestation"],
)
def test_golden_parity(fixture_fn, golden: str) -> None:
    assert implement_spec_hash(fixture_fn()) == golden


# T2 — round-trip identity
def test_round_trip_identity_model_dump_json() -> None:
    spec = _ready_spec()
    h0 = implement_spec_hash(spec)
    restored = ImplementSpec.model_validate_json(spec.model_dump_json())
    assert implement_spec_hash(restored) == h0


def test_round_trip_identity_json_loads() -> None:
    spec = _ready_spec()
    h0 = implement_spec_hash(spec)
    restored = ImplementSpec.model_validate(json.loads(spec.model_dump_json()))
    assert implement_spec_hash(restored) == h0


# T3 — self/attestation elision
def test_finalize_spec_hash_unchanged() -> None:
    spec = _ready_spec()
    h0 = implement_spec_hash(spec)
    assert implement_spec_hash(finalize_spec(spec)) == h0


def test_bound_review_attestation_hash_unchanged() -> None:
    spec = _ready_spec()
    h0 = implement_spec_hash(spec)
    att = ReviewAttestation(
        spec_hash=h0,
        disposition="pass",
        required=True,
        risk_tier="material",
        reviewer_family="claude",
    )
    stamped = spec.model_copy(
        update={
            "provenance": spec.provenance.model_copy(update={"review_attestation": att})
        }
    )
    assert implement_spec_hash(stamped) == h0


# T4 — volatile identity
def test_volatile_created_at_ignored() -> None:
    spec = _ready_spec()
    h0 = implement_spec_hash(spec)
    varied = spec.model_copy(
        update={
            "provenance": spec.provenance.model_copy(
                update={"created_at": datetime(1999, 1, 1, tzinfo=UTC)}
            )
        }
    )
    assert implement_spec_hash(varied) == h0


def test_volatile_freshness_checked_at_ignored() -> None:
    spec = _ready_spec()
    h0 = implement_spec_hash(spec)
    varied = _ready_spec(
        readiness=Readiness(
            state=ReadinessState.READY,
            freshness_checked_at=datetime(1999, 6, 1, tzinfo=UTC),
        )
    )
    assert implement_spec_hash(varied) == h0


def test_volatile_deck_body_ignored() -> None:
    spec = _ready_spec(scope=Scope(deck_body="volatile deck text"))
    h0 = implement_spec_hash(spec)
    varied = _ready_spec(scope=Scope(deck_body="different deck text"))
    assert implement_spec_hash(varied) == h0


def test_volatile_source_uri_ignored() -> None:
    spec = _ready_spec(
        source=Source(
            source_ref="todo:foo",
            canonical_ref="todo:foo",
            source_kind=SourceKind.TODO,
            source_uri="path/a",
            source_version=SourceVersion(content_hash="sha256:abc"),
        )
    )
    h0 = implement_spec_hash(spec)
    varied = _ready_spec(
        source=Source(
            source_ref="todo:foo",
            canonical_ref="todo:foo",
            source_kind=SourceKind.TODO,
            source_uri="path/b",
            source_version=SourceVersion(content_hash="sha256:abc"),
        )
    )
    assert implement_spec_hash(varied) == h0


def test_volatile_deck_sha256_none_elided() -> None:
    spec = _ready_spec(
        source=Source(
            source_ref="todo:foo",
            canonical_ref="todo:foo",
            source_kind=SourceKind.TODO,
            source_version=SourceVersion(
                content_hash="sha256:abc",
                deck_sha256=None,
            ),
        )
    )
    h0 = implement_spec_hash(spec)
    varied = _ready_spec(
        source=Source(
            source_ref="todo:foo",
            canonical_ref="todo:foo",
            source_kind=SourceKind.TODO,
            source_version=SourceVersion(content_hash="sha256:abc"),
        )
    )
    assert implement_spec_hash(varied) == h0


# T5 — optional-absent vs explicit-None identity
@pytest.mark.parametrize(
    "mutator",
    [
        lambda s: s,
        lambda s: s.model_copy(
            update={
                "source": s.source.model_copy(
                    update={"parent_ref": None, "selector": None, "source_uri": None}
                ),
                "intent": s.intent.model_copy(update={"description": None}),
                "readiness": s.readiness.model_copy(update={"gated_reason": None}),
                "routing": s.routing.model_copy(
                    update={"requested_execution_mode": None}
                )
                if s.routing
                else None,
                "closeout": s.closeout.model_copy(update={"bus_thread": None}),
            }
        ),
    ],
    ids=["absent_defaults", "explicit_none"],
)
def test_optional_absent_vs_explicit_none(mutator) -> None:
    spec = _ready_spec()
    h0 = implement_spec_hash(spec)
    assert implement_spec_hash(mutator(spec)) == h0


def test_gated_optional_absent_vs_explicit_none() -> None:
    spec = _gated_spec()
    h0 = implement_spec_hash(spec)
    explicit = spec.model_copy(
        update={
            "source": spec.source.model_copy(
                update={"parent_ref": None, "selector": None, "source_uri": None}
            ),
            "intent": spec.intent.model_copy(update={"description": None}),
            "closeout": spec.closeout.model_copy(update={"bus_thread": None}),
        }
    )
    assert implement_spec_hash(explicit) == h0


# T6 — key-reorder identity
def test_key_reorder_identity() -> None:
    payload_a = {
        "schema_version": 1,
        "acceptance": {"criteria": ["done"]},
        "closeout": {"adapter": "todo"},
        "intent": {"summary": "test"},
        "provenance": {},
        "readiness": {"state": "ready"},
        "routing": {
            "checkpoint_required": False,
            "derivation": {"mode_rule": "m", "style_rule": "s"},
            "executor_style": "mechanical",
            "orchestration_mode": "single",
        },
        "scope": {"bounded": True, "files_expected": []},
        "skills": [],
        "source": {
            "canonical_ref": "todo:foo",
            "source_kind": "todo",
            "source_ref": "todo:foo",
            "source_version": {"content_hash": "sha256:abc"},
        },
    }
    payload_b = {
        "source": {
            "source_ref": "todo:foo",
            "canonical_ref": "todo:foo",
            "source_kind": "todo",
            "source_version": {"content_hash": "sha256:abc"},
        },
        "skills": [],
        "scope": {"files_expected": [], "bounded": True},
        "routing": {
            "orchestration_mode": "single",
            "executor_style": "mechanical",
            "checkpoint_required": False,
            "derivation": {"style_rule": "s", "mode_rule": "m"},
        },
        "readiness": {"state": "ready"},
        "provenance": {},
        "intent": {"summary": "test"},
        "closeout": {"adapter": "todo"},
        "acceptance": {"criteria": ["done"]},
        "schema_version": 1,
    }
    spec_a = ImplementSpec.model_validate(payload_a)
    spec_b = ImplementSpec.model_validate(payload_b)
    assert implement_spec_hash(spec_a) == implement_spec_hash(spec_b)


# T7 — list-order is semantic
def test_skills_order_is_semantic() -> None:
    a = _ready_spec(skills=["alpha", "beta"])
    b = _ready_spec(skills=["beta", "alpha"])
    assert implement_spec_hash(a) != implement_spec_hash(b)


def test_files_expected_order_is_semantic() -> None:
    a = _ready_spec(scope=Scope(files_expected=["a.py", "b.py"]))
    b = _ready_spec(scope=Scope(files_expected=["b.py", "a.py"]))
    assert implement_spec_hash(a) != implement_spec_hash(b)


def test_acceptance_criteria_order_is_semantic() -> None:
    a = _ready_spec(acceptance=Acceptance(criteria=["first", "second"]))
    b = _ready_spec(acceptance=Acceptance(criteria=["second", "first"]))
    assert implement_spec_hash(a) != implement_spec_hash(b)


# T8 — semantic negative controls
@pytest.mark.parametrize(
    "mutator",
    [
        lambda s: s.model_copy(
            update={"source": s.source.model_copy(update={"source_ref": "todo:bar"})}
        ),
        lambda s: s.model_copy(
            update={"source": s.source.model_copy(update={"canonical_ref": "todo:bar"})}
        ),
        lambda s: s.model_copy(
            update={
                "source": s.source.model_copy(update={"source_kind": SourceKind.PLAN})
            }
        ),
        lambda s: s.model_copy(
            update={
                "source": s.source.model_copy(
                    update={
                        "source_version": s.source.source_version.model_copy(
                            update={"content_hash": "sha256:changed"}
                        )
                    }
                )
            }
        ),
        lambda s: s.model_copy(
            update={
                "source": s.source.model_copy(
                    update={
                        "source_version": s.source.source_version.model_copy(
                            update={"packet_sha256": "sha256:changed"}
                        )
                    }
                )
            }
        ),
        lambda s: s.model_copy(
            update={"intent": s.intent.model_copy(update={"summary": "changed"})}
        ),
        lambda s: s.model_copy(
            update={"scope": s.scope.model_copy(update={"bounded": False})}
        ),
        lambda s: _gated_spec(),
        lambda s: s.model_copy(
            update={
                "routing": s.routing.model_copy(
                    update={"orchestration_mode": OrchestrationMode.COORDINATOR}
                )
            }
        ),
        lambda s: s.model_copy(
            update={
                "routing": s.routing.model_copy(
                    update={"executor_style": ExecutorStyle.REASONING}
                )
            }
        ),
        lambda s: s.model_copy(
            update={
                "routing": s.routing.model_copy(update={"checkpoint_required": True})
            }
        ),
        lambda s: s.model_copy(
            update={"acceptance": Acceptance(criteria=["changed", "done"])}
        ),
        lambda s: s.model_copy(
            update={
                "closeout": s.closeout.model_copy(
                    update={"adapter": CloseoutAdapterKind.PLAN}
                )
            }
        ),
    ],
    ids=[
        "source_ref",
        "canonical_ref",
        "source_kind",
        "content_hash",
        "packet_sha256",
        "intent_summary",
        "scope_bounded",
        "readiness_state",
        "orchestration_mode",
        "executor_style",
        "checkpoint_required",
        "acceptance_criteria_0",
        "closeout_adapter",
    ],
)
def test_semantic_negative_controls(mutator) -> None:
    base = _ready_spec()
    h0 = implement_spec_hash(base)
    assert implement_spec_hash(mutator(base)) != h0


# T9 — fail-closed encoder
def test_canonical_default_raises_on_unknown_type() -> None:
    with pytest.raises(TypeError, match="non-canonical type"):
        _canonical_default(object())


def test_canonical_default_sorts_sets_deterministically() -> None:
    # No current ImplementSpec field is a set; guard future set encoding anyway.
    encoded = _canonical_default({3, 1, 2})
    assert encoded == [1, 2, 3]


# T10 — schema-participation guard
def test_schema_participation_guard() -> None:
    leaf_paths = _enumerate_leaf_paths(ImplementSpec)
    allowed = PARTICIPATING_PATHS | ELIDED_PATHS
    unclassified = leaf_paths - allowed
    assert not unclassified, (
        "New ImplementSpec fields must be classified as PARTICIPATING or ELIDED: "
        f"{sorted(unclassified)}"
    )
