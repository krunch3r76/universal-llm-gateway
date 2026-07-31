"""Tests for the Stargate implement-readiness dispatch adapter."""

from __future__ import annotations

from pathlib import Path

import pytest
from implement_admission.dense_spec_schema import dense_spec_hash_uri, dense_spec_sha256

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.implement_ready_gate import (
    _read_dense_spec_text,
    require_implement_ready,
)

_TODO = "todo:densification-implement-admission-gate"
_SPEC = "cortex://notes/system/specs/densification-implement-admission-gate.md"

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
        assertions: list[dict] | None = None,
        bus_turns: dict[tuple[str, int], dict[str, object]] | None = None,
        bus_last_turns: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self._entity = entity or {}
        self._assertion = assertion
        self._assertions = assertions or []
        self._bus_turns = bus_turns or {}
        self._bus_last_turns = bus_last_turns or {}

    def entity_get(self, entity_id: str, **_kwargs: object) -> dict:
        return self._entity

    def assertion_get(self, assertion_id: int) -> dict:
        if self._assertion is None:
            return {"error": "missing"}
        return self._assertion

    def assertions(self, entity_id: str, **_kwargs: object) -> dict:
        return {"items": list(self._assertions)}

    def bus_turn_get(self, thread: str, turn_number: int) -> dict | None:
        return self._bus_turns.get((str(thread), int(turn_number)))

    def bus_thread_last_turn(self, thread: str) -> dict | None:
        return self._bus_last_turns.get(str(thread))


def _entity(
    *,
    density_triage: str | None = None,
    assertion_id: int | None = None,
    source_uri: str | None = _SPEC,
    files_expected: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    name: str | None = "densification-implement-admission-gate",
    recon_waived: str | None = None,
) -> dict:
    attrs: dict = {}
    if density_triage is not None:
        attrs["density_triage"] = density_triage
    if assertion_id is not None:
        attrs["implement_ready_assertion_id"] = assertion_id
    if files_expected is not None:
        attrs["files_expected"] = files_expected
    if acceptance_criteria is not None:
        attrs["acceptance_criteria"] = acceptance_criteria
    if recon_waived is not None:
        attrs["recon_waived"] = recon_waived
    return {
        "attributes": attrs,
        "source_uri": source_uri,
        "name": name,
    }


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


def _ir_row(
    spec_text: str = _VALID_DENSE_SPEC,
    *,
    aid: int = 200,
    **overrides: object,
) -> dict:
    """A full active confirmed implement-ready assertion row (assertions list)."""
    row = {
        "id": aid,
        "entity_id": _TODO,
        "superseded_by": None,
        "valid_until": None,
        "observed_at": "2026-06-17T07:00:00Z",
        "predicate_form": f"status({_TODO}, implement_ready, current)",
        "evidence_uris": [
            f"workspaces://universal-llm-gateway/{_SPEC}",
            dense_spec_hash_uri(spec_text),
        ],
    }
    row.update(overrides)
    return row


def _skeptic_row(
    spec_text: str = _VALID_DENSE_SPEC,
    *,
    aid: int = 300,
    **overrides: object,
) -> dict:
    """A skeptic-ratification row pinned to the dense-spec content hash (P2)."""
    row = {
        "id": aid,
        "entity_id": _TODO,
        "superseded_by": None,
        "valid_until": None,
        "observed_at": "2026-06-17T08:00:00Z",
        "predicate_form": f"status({_TODO}, skeptic_ratified, current)",
        "evidence_uris": [dense_spec_hash_uri(spec_text)],
    }
    row.update(overrides)
    return row


def _skeptic_body(*paths: str, reasoning_only: bool = False) -> str:
    lines = ["VERDICT: RATIFIED", ""]
    if reasoning_only:
        lines.extend(["grounding_mode: reasoning_only", ""])
        return "\n".join(lines)
    lines.append("FILE_EVIDENCE_PATHS:")
    lines.extend(paths)
    return "\n".join(lines)


def _post_cutoff_skeptic_row(
    spec_text: str = _VALID_DENSE_SPEC,
    *,
    aid: int = 300,
    thread: str = "3571",
    turn: int = 2,
    **overrides: object,
) -> dict:
    row = _skeptic_row(
        spec_text,
        aid=aid,
        observed_at="2026-06-29T12:00:00+00:00",
        evidence_uris=[
            dense_spec_hash_uri(spec_text),
            f"agent-bus:{thread}#turn-{turn}",
        ],
    )
    row.update(overrides)
    return row


def _judgment_ready_cortex(
    *,
    assertion: dict,
    assertions: list[dict],
    bus_turns: dict[tuple[str, int], dict[str, object]] | None = None,
) -> _FakeCortex:
    return _FakeCortex(
        entity=_entity(
            density_triage="judgment_required",
            assertion_id=99,
            files_expected=["module.py"],
            acceptance_criteria=["Validator passes dense specs."],
        ),
        assertion=assertion,
        assertions=assertions,
        bus_turns=bus_turns,
    )


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
def test_recon_pending_raises() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(entity=_entity(density_triage="recon_pending")),
        )
    assert exc.value.code == "implement_blocked_recon_pending"


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
            entity=_entity(
                density_triage="judgment_required",
                assertion_id=99,
                files_expected=["module.py"],
                acceptance_criteria=["Validator passes dense specs."],
            ),
            assertion=_assertion(),
            assertions=[_skeptic_row()],
        ),
    )


@pytest.mark.offline
def test_stale_skeptic_ratification_not_pinned_to_spec_rejects(spec_file: Path) -> None:
    """P2: a skeptic pass on a prior spec version cannot re-admit a new spec.

    The skeptic row is active and predicate-correct but cites only a stale spec
    hash, not the current dense-spec content. The gate must require a fresh
    skeptic pass (skeptic_pass_missing) rather than reuse the stale attestation.
    """
    stale = _skeptic_row(evidence_uris=["spec_sha256:deadbeefdeadbeef"])
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(
                entity=_entity(
                    density_triage="judgment_required",
                    assertion_id=99,
                    files_expected=["module.py"],
                    acceptance_criteria=["Validator passes dense specs."],
                ),
                assertion=_assertion(),
                assertions=[stale],
            ),
        )
    assert exc.value.code == "skeptic_pass_missing"


@pytest.mark.offline
def test_recon_waived_material_admits_without_skeptic(spec_file: Path) -> None:
    require_implement_ready(
        request_id="req-1",
        source_ref=_TODO,
        cortex=_FakeCortex(
            entity=_entity(
                density_triage="judgment_required",
                assertion_id=99,
                files_expected=["module.py"],
                acceptance_criteria=["Validator passes dense specs."],
                recon_waived="operator waived axis-2 for this arc",
            ),
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
                entity=_entity(
                    density_triage="judgment_required",
                    assertion_id=99,
                    files_expected=["module.py"],
                    acceptance_criteria=["Validator passes dense specs."],
                ),
                assertion=_assertion(
                    evidence_uris=[f"workspaces://universal-llm-gateway/{_SPEC}"],
                ),
            ),
        )
    assert exc.value.code == "implement_spec_drifted_since_ready"


@pytest.mark.offline
def test_unpopulated_attrs_raises_before_materialization(spec_file: Path) -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(
                entity=_entity(
                    density_triage="judgment_required",
                    assertion_id=99,
                    files_expected=[],
                    acceptance_criteria=[],
                ),
                assertion=_assertion(),
            ),
        )
    assert exc.value.code == "implement_attrs_unpopulated"
    assert exc.value.status_code == 422


@pytest.mark.offline
def test_stale_pin_resolves_fresh_implement_ready(spec_file: Path) -> None:
    """Friction 19783: superseded pin + fresh active implement-ready admits."""
    require_implement_ready(
        request_id="req-1",
        source_ref=_TODO,
        cortex=_FakeCortex(
            entity=_entity(
                density_triage="judgment_required",
                assertion_id=99,
                files_expected=["module.py"],
                acceptance_criteria=["Validator passes dense specs."],
            ),
            assertion=_assertion(superseded_by=100),
            assertions=[_ir_row(aid=200), _skeptic_row()],
        ),
    )


@pytest.mark.offline
def test_absent_pin_resolves_fresh_implement_ready(spec_file: Path) -> None:
    """No pinned id at all, but a fresh active implement-ready exists -> admits."""
    require_implement_ready(
        request_id="req-1",
        source_ref=_TODO,
        cortex=_FakeCortex(
            entity=_entity(
                density_triage="judgment_required",
                files_expected=["module.py"],
                acceptance_criteria=["Validator passes dense specs."],
            ),
            assertion=None,
            assertions=[_ir_row(aid=200), _skeptic_row()],
        ),
    )


@pytest.mark.offline
def test_stale_pin_without_fresh_implement_ready_still_rejects(
    spec_file: Path,
) -> None:
    """Superseded pin + only an `implemented` row (no implement-ready) rejects.

    Proves the resolver keys off the implement_ready predicate and will not pick
    a later `implemented` confirmed row (the reason Strategy B is unsafe).
    """
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(
                entity=_entity(
                    density_triage="judgment_required",
                    assertion_id=99,
                    files_expected=["module.py"],
                    acceptance_criteria=["Validator passes dense specs."],
                ),
                assertion=_assertion(superseded_by=100),
                assertions=[
                    _ir_row(
                        aid=200,
                        predicate_form=f"status({_TODO}, implemented, current)",
                    )
                ],
            ),
        )
    assert exc.value.code == "implement_ready_assertion_inactive"


@pytest.mark.offline
def test_inactive_resolved_candidate_is_skipped(spec_file: Path) -> None:
    """A resolver candidate that is itself superseded does not get selected."""
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(
                entity=_entity(
                    density_triage="judgment_required",
                    assertion_id=99,
                    files_expected=["module.py"],
                    acceptance_criteria=["Validator passes dense specs."],
                ),
                assertion=_assertion(superseded_by=100),
                assertions=[_ir_row(aid=200, superseded_by=201)],
            ),
        )
    assert exc.value.code == "implement_ready_assertion_inactive"


@pytest.mark.offline
@pytest.mark.parametrize(
    "cited_uri",
    [
        "tasks/specs/densification-implement-admission-gate.md",
        "workspaces:universal-llm-gateway/tasks/specs/densification-implement-admission-gate.md",
        "workspaces://universal-llm-gateway/tasks/specs/densification-implement-admission-gate.md",
        "ws:universal-llm-gateway/tasks/specs/densification-implement-admission-gate.md",
        "ws://universal-llm-gateway/tasks/specs/densification-implement-admission-gate.md",
    ],
)
def test_read_dense_spec_text_workspaces_scheme_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cited_uri: str,
) -> None:
    spec_dir = tmp_path / "universal-llm-gateway" / "tasks" / "specs"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "densification-implement-admission-gate.md"
    spec_path.write_text(_VALID_DENSE_SPEC, encoding="utf-8")
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))

    text = _read_dense_spec_text(cited_uri)
    assert text == _VALID_DENSE_SPEC


@pytest.mark.offline
@pytest.mark.parametrize(
    "cited_uri",
    [
        "cortex:notes/system/specs/densification-implement-admission-gate.md",
        "cortex://notes/system/specs/densification-implement-admission-gate.md",
    ],
)
def test_read_dense_spec_text_cortex_scheme_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cited_uri: str,
) -> None:
    cortex_root = tmp_path / "cortex"
    spec_dir = cortex_root / "notes" / "system" / "specs"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "densification-implement-admission-gate.md"
    spec_path.write_text(_VALID_DENSE_SPEC, encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))

    text = _read_dense_spec_text(cited_uri)
    assert text == _VALID_DENSE_SPEC


@pytest.mark.offline
def test_cortex_spec_admits_with_spec_sha256_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = "densification-implement-admission-gate"
    cortex_root = tmp_path / "cortex"
    spec_dir = cortex_root / "notes" / "system" / "specs"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / f"{slug}.md"
    spec_path.write_text(_VALID_DENSE_SPEC, encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))

    cited = f"cortex://notes/system/specs/{slug}.md"
    require_implement_ready(
        request_id="req-1",
        source_ref=_TODO,
        cortex=_FakeCortex(
            entity=_entity(
                density_triage="judgment_required",
                assertion_id=99,
                source_uri=f"notes/system/specs/{slug}.md",
                files_expected=["module.py"],
                acceptance_criteria=["Validator passes dense specs."],
            ),
            assertion=_assertion(
                evidence_uris=[
                    cited,
                    dense_spec_hash_uri(_VALID_DENSE_SPEC),
                ],
            ),
            assertions=[_skeptic_row()],
        ),
    )


@pytest.mark.offline
def test_cortex_spec_rejects_sha256_only_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = "densification-implement-admission-gate"
    cortex_root = tmp_path / "cortex"
    spec_dir = cortex_root / "notes" / "system" / "specs"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / f"{slug}.md"
    spec_path.write_text(_VALID_DENSE_SPEC, encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))

    cited = f"cortex://notes/system/specs/{slug}.md"
    digest = dense_spec_sha256(_VALID_DENSE_SPEC)
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_FakeCortex(
                entity=_entity(
                    density_triage="judgment_required",
                    assertion_id=99,
                    source_uri=f"notes/system/specs/{slug}.md",
                    files_expected=["module.py"],
                    acceptance_criteria=["Validator passes dense specs."],
                ),
                assertion=_assertion(
                    evidence_uris=[
                        cited,
                        f"sha256:{digest}",
                    ],
                ),
            ),
        )
    assert exc.value.code == "implement_spec_drifted_since_ready"


@pytest.mark.offline
def test_post_cutoff_grounded_skeptic_evidence_admits(spec_file: Path) -> None:
    cited = f"workspaces://universal-llm-gateway/{_SPEC}"
    body = _skeptic_body(cited, "agent-bus:3571", "spec_sha256:ignored")
    require_implement_ready(
        request_id="req-1",
        source_ref=_TODO,
        cortex=_judgment_ready_cortex(
            assertion=_assertion(),
            assertions=[_post_cutoff_skeptic_row()],
            bus_turns={("3571", 2): {"body": body}},
        ),
    )


@pytest.mark.offline
def test_post_cutoff_unresolved_skeptic_path_rejects(spec_file: Path) -> None:
    body = _skeptic_body("workspaces://universal-llm-gateway/tasks/specs/missing.md")
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_judgment_ready_cortex(
                assertion=_assertion(),
                assertions=[_post_cutoff_skeptic_row()],
                bus_turns={("3571", 2): {"body": body}},
            ),
        )
    assert exc.value.code == "skeptic_evidence_unresolved"
    assert "missing.md" in exc.value.reason


@pytest.mark.offline
def test_post_cutoff_missing_file_evidence_block_rejects(spec_file: Path) -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_judgment_ready_cortex(
                assertion=_assertion(),
                assertions=[_post_cutoff_skeptic_row()],
                bus_turns={("3571", 2): {"body": "VERDICT: RATIFIED\n"}},
            ),
        )
    assert exc.value.code == "skeptic_evidence_missing"


@pytest.mark.offline
def test_post_cutoff_reasoning_only_rejects(spec_file: Path) -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_judgment_ready_cortex(
                assertion=_assertion(),
                assertions=[_post_cutoff_skeptic_row()],
                bus_turns={("3571", 2): {"body": _skeptic_body(reasoning_only=True)}},
            ),
        )
    assert exc.value.code == "skeptic_evidence_missing"


@pytest.mark.offline
def test_post_cutoff_malformed_file_scheme_rejects(spec_file: Path) -> None:
    body = _skeptic_body("cotex://notes/system/specs/fake.md")
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_judgment_ready_cortex(
                assertion=_assertion(),
                assertions=[_post_cutoff_skeptic_row()],
                bus_turns={("3571", 2): {"body": body}},
            ),
        )
    assert exc.value.code == "skeptic_evidence_malformed"


@pytest.mark.offline
def test_post_cutoff_traversal_path_rejects(spec_file: Path) -> None:
    body = _skeptic_body("../../etc/passwd")
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_judgment_ready_cortex(
                assertion=_assertion(),
                assertions=[_post_cutoff_skeptic_row()],
                bus_turns={("3571", 2): {"body": body}},
            ),
        )
    assert exc.value.code == "skeptic_evidence_unresolved"


@pytest.mark.offline
def test_post_cutoff_bus_fetch_missing_rejects(spec_file: Path) -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_implement_ready(
            request_id="req-1",
            source_ref=_TODO,
            cortex=_judgment_ready_cortex(
                assertion=_assertion(),
                assertions=[_post_cutoff_skeptic_row()],
            ),
        )
    assert exc.value.code == "skeptic_evidence_stamp_missing"


@pytest.mark.offline
def test_pre_cutoff_skeptic_grandfathered_without_bus_fetch(spec_file: Path) -> None:
    require_implement_ready(
        request_id="req-1",
        source_ref=_TODO,
        cortex=_FakeCortex(
            entity=_entity(
                density_triage="judgment_required",
                assertion_id=99,
                files_expected=["module.py"],
                acceptance_criteria=["Validator passes dense specs."],
            ),
            assertion=_assertion(),
            assertions=[_skeptic_row()],
        ),
    )
