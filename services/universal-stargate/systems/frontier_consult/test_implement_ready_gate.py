"""Tests for the Stargate implement-readiness dispatch adapter."""

from __future__ import annotations

from pathlib import Path

import pytest
from implement_admission.dense_spec_schema import dense_spec_hash_uri

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.implement_ready_gate import require_implement_ready

_TODO = "todo:densification-implement-admission-gate"
_SPEC = "tasks/specs/densification-implement-admission-gate.md"

_VALID_DENSE_SPEC = """\
# Dense test spec

## 1. Problem

A problem exists.

## 2. Non-goals / scope exclusions

Out of scope items.

## 3. Source-of-truth / provenance

| Source | Role |
|---|---|
| spec | authoritative |

## 4. Touch-point inventory

- module.py

## 5. Bound design decisions / fork table

| Fork | Decision |
|---|---|
| 1 | resolved |

## 6. Implementation guidance

Build the validator.

## 7. Acceptance criteria

1. Validator passes dense specs.

## 8. Verification / quality gates

- pytest green

<reasoning_trace>

No fork remains OPEN.

</reasoning_trace>
"""


class _FakeCortex:
    def __init__(
        self,
        *,
        entity: dict | None = None,
        assertion: dict | None = None,
    ) -> None:
        self._entity = entity or {}
        self._assertion = assertion

    def entity_get(self, entity_id: str, **_kwargs: object) -> dict:
        return self._entity

    def assertion_get(self, assertion_id: int) -> dict:
        if self._assertion is None:
            return {"error": "missing"}
        return self._assertion


def _entity(
    *,
    density_triage: str | None = None,
    assertion_id: int | None = None,
    source_uri: str | None = _SPEC,
) -> dict:
    attrs: dict = {}
    if density_triage is not None:
        attrs["density_triage"] = density_triage
    if assertion_id is not None:
        attrs["implement_ready_assertion_id"] = assertion_id
    return {"attributes": attrs, "source_uri": source_uri}


def _assertion(spec_text: str = _VALID_DENSE_SPEC, **overrides: object) -> dict:
    base = {
        "entity_id": _TODO,
        "superseded_by": None,
        "valid_until": None,
        "evidence_uris": [
            f"workspaces://universal-llm-gateway/{_SPEC}",
            dense_spec_hash_uri(spec_text),
        ],
    }
    base.update(overrides)
    return base


@pytest.fixture()
def spec_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    spec_dir = tmp_path / "universal-llm-gateway" / "tasks" / "specs"
    spec_dir.mkdir(parents=True)
    path = spec_dir / "densification-implement-admission-gate.md"
    path.write_text(_VALID_DENSE_SPEC, encoding="utf-8")
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    return path


@pytest.mark.offline
def test_non_todo_source_ref_is_noop() -> None:
    require_implement_ready(
        request_id="req-1",
        source_ref="packet:tmp/reviews/packet.md",
        cortex=_FakeCortex(entity=_entity(density_triage="unknown")),
    )


@pytest.mark.offline
def test_none_source_ref_is_noop() -> None:
    require_implement_ready(
        request_id="req-1",
        source_ref=None,
        cortex=_FakeCortex(),
    )


@pytest.mark.offline
def test_judgment_required_without_assertion_raises() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=f"todo:{_TODO.split(':', 1)[1]}",
            cortex=_FakeCortex(entity=_entity(density_triage="judgment_required")),
        )
    assert exc.value.code == "implement_not_ready_judgment_required"
    assert exc.value.status_code == 422


@pytest.mark.offline
def test_unknown_triage_raises() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(entity=_entity(density_triage="unknown")),
        )
    assert exc.value.code == "implement_triage_unknown"


@pytest.mark.offline
def test_mechanical_admits() -> None:
    require_implement_ready(
        request_id="req-1",
        source_ref=_TODO,
        cortex=_FakeCortex(entity=_entity(density_triage="mechanical")),
    )


@pytest.mark.offline
def test_judgment_required_with_valid_assertion_admits(spec_file: Path) -> None:
    require_implement_ready(
        request_id="req-1",
        source_ref=_TODO,
        cortex=_FakeCortex(
            entity=_entity(density_triage="judgment_required", assertion_id=99),
            assertion=_assertion(),
        ),
    )


@pytest.mark.offline
def test_superseded_assertion_raises(spec_file: Path) -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(
                entity=_entity(density_triage="judgment_required", assertion_id=99),
                assertion=_assertion(superseded_by=100),
            ),
        )
    assert exc.value.code == "implement_ready_assertion_inactive"


@pytest.mark.offline
def test_seed_contract_ack_does_not_bypass() -> None:
    entity = _entity(density_triage="judgment_required")
    entity["attributes"]["seed_contract_ack"] = "ack"
    entity["attributes"]["backlog"] = True
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(entity=entity),
        )
    assert exc.value.code == "implement_not_ready_judgment_required"


@pytest.mark.offline
def test_unreadable_spec_raises(spec_file: Path) -> None:
    spec_file.unlink()
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(
                entity=_entity(density_triage="judgment_required", assertion_id=99),
                assertion=_assertion(),
            ),
        )
    assert exc.value.code == "implement_spec_unreadable"


@pytest.mark.offline
def test_sparse_spec_content_raises(spec_file: Path) -> None:
    spec_file.write_text(
        "# Sparse\n\n## Problem\n\nOnly one section.\n", encoding="utf-8"
    )
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(
                entity=_entity(density_triage="judgment_required", assertion_id=99),
                assertion=_assertion(),
            ),
        )
    assert exc.value.code == "implement_spec_not_dense"


@pytest.mark.offline
def test_hash_drift_raises(spec_file: Path) -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(
                entity=_entity(density_triage="judgment_required", assertion_id=99),
                assertion=_assertion(
                    evidence_uris=[f"workspaces://universal-llm-gateway/{_SPEC}"],
                ),
            ),
        )
    assert exc.value.code == "implement_spec_drifted_since_ready"
