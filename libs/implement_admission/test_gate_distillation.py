"""Unit tests for Gate-2 implement-admission distillation helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.gate_distillation import (
    GateDistillationFailure,
    GateDistillationInputs,
    build_implement_ready_evidence_uris,
    default_dense_spec_uri,
    normalize_dense_spec_path,
    prepare_gate_distillation,
    read_dense_spec_text,
    resolve_dense_spec_path,
    todo_slug,
)

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


def _write_cortex_spec(
    tmp_path: Path, slug: str, text: str = _VALID_DENSE_SPEC
) -> Path:
    spec_dir = tmp_path / "notes" / "system" / "specs"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / f"{slug}.md"
    spec_file.write_text(text, encoding="utf-8")
    return spec_file


def _write_cortex_spec_named(
    tmp_path: Path, basename: str, text: str = _VALID_DENSE_SPEC
) -> Path:
    spec_dir = tmp_path / "notes" / "system" / "specs"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / basename
    spec_file.write_text(text, encoding="utf-8")
    return spec_file


def _write_workspace_spec(
    tmp_path: Path, slug: str, text: str = _VALID_DENSE_SPEC
) -> Path:
    spec_dir = tmp_path / "universal-llm-gateway" / "tasks" / "specs"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / f"{slug}.md"
    spec_file.write_text(text, encoding="utf-8")
    return spec_file


@pytest.fixture
def cortex_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    return tmp_path


@pytest.mark.offline
def test_todo_slug_and_default_spec_uri() -> None:
    assert todo_slug("todo:wire-gate") == "wire-gate"
    assert default_dense_spec_uri("todo:wire-gate") == "notes/system/specs/wire-gate.md"


@pytest.mark.offline
def test_resolve_as_cited() -> None:
    resolution = resolve_dense_spec_path(
        "cortex://notes/system/specs/wire-gate.md",
        todo_id="todo:wire-gate",
    )
    assert resolution.action == "as_cited"
    assert resolution.basename_nonstandard is False
    assert resolution.resolved == "cortex://notes/system/specs/wire-gate.md"


@pytest.mark.offline
def test_resolve_scheme_normalized_single_colon() -> None:
    resolution = resolve_dense_spec_path(
        "cortex:notes/system/specs/custom.md",
        todo_id="todo:wire-gate",
    )
    assert resolution.action == "scheme_normalized"
    assert resolution.resolved == "cortex://notes/system/specs/custom.md"
    assert resolution.basename_nonstandard is True


@pytest.mark.offline
def test_resolve_bare_notes_path() -> None:
    resolution = resolve_dense_spec_path(
        "notes/system/specs/wire-gate.md",
        todo_id="todo:wire-gate",
    )
    assert resolution.action == "scheme_normalized"
    assert resolution.resolved == "cortex://notes/system/specs/wire-gate.md"


@pytest.mark.offline
def test_resolve_files_prefix() -> None:
    resolution = resolve_dense_spec_path(
        "files://notes/system/specs/wire-gate.md",
        todo_id="todo:wire-gate",
    )
    assert resolution.action == "scheme_normalized"
    assert resolution.resolved == "cortex://notes/system/specs/wire-gate.md"


@pytest.mark.offline
def test_resolve_relocated_retired_home() -> None:
    resolution = resolve_dense_spec_path(
        "workspaces://universal-llm-gateway/tasks/specs/custom.md",
        todo_id="todo:wire-gate",
    )
    assert resolution.action == "relocated_retired_home"
    assert resolution.resolved == "cortex://notes/system/specs/custom.md"
    assert resolution.basename_nonstandard is True


@pytest.mark.offline
def test_resolve_defaulted_empty_source() -> None:
    resolution = resolve_dense_spec_path(None, todo_id="todo:wire-gate")
    assert resolution.action == "defaulted_empty_source"
    assert resolution.cited is None
    assert resolution.resolved == "cortex://notes/system/specs/wire-gate.md"


@pytest.mark.offline
@pytest.mark.parametrize(
    "source_uri",
    [
        "agent-bus:588",
        "cortex://notes/system/threads/x.md",
        "notes/system/specs/group/x.md",
    ],
)
def test_resolve_defaulted_non_spec_source(source_uri: str) -> None:
    resolution = resolve_dense_spec_path(source_uri, todo_id="todo:wire-gate")
    assert resolution.action == "defaulted_non_spec_source"
    assert resolution.resolved == "cortex://notes/system/specs/wire-gate.md"


@pytest.mark.offline
def test_normalize_dense_spec_path_preserves_non_matching_basename() -> None:
    assert (
        normalize_dense_spec_path(
            "workspaces://universal-llm-gateway/tasks/specs/custom.md",
            todo_id="todo:wire-gate",
        )
        == "cortex://notes/system/specs/custom.md"
    )


@pytest.mark.offline
def test_normalize_dense_spec_path_defaults_to_cortex() -> None:
    assert (
        normalize_dense_spec_path(None, todo_id="todo:wire-gate")
        == "cortex://notes/system/specs/wire-gate.md"
    )


@pytest.mark.offline
def test_normalize_dense_spec_path_preserves_cortex_uri() -> None:
    assert (
        normalize_dense_spec_path(
            "cortex://notes/system/specs/wire-gate.md",
            todo_id="todo:wire-gate",
        )
        == "cortex://notes/system/specs/wire-gate.md"
    )


@pytest.mark.offline
def test_normalize_dense_spec_path_emits_cortex_for_bare_notes_path() -> None:
    assert (
        normalize_dense_spec_path(
            "notes/system/specs/wire-gate.md",
            todo_id="todo:wire-gate",
        )
        == "cortex://notes/system/specs/wire-gate.md"
    )


@pytest.mark.offline
def test_normalize_dense_spec_path_rewrites_workspace_citation_to_cortex() -> None:
    """``tasks/specs`` is retired as an authoring locus — always rewrite to Cortex."""
    assert (
        normalize_dense_spec_path(
            "workspaces://universal-llm-gateway/tasks/specs/wire-gate.md",
            todo_id="todo:wire-gate",
        )
        == "cortex://notes/system/specs/wire-gate.md"
    )


@pytest.mark.offline
def test_prepare_success_descriptive_basename(
    cortex_root: Path,
    tmp_path: Path,
) -> None:
    descriptive = "friction-26462-cursor-auto-auth-gate-budget.md"
    _write_cortex_spec_named(cortex_root, descriptive)

    prepared = prepare_gate_distillation(
        todo_id="todo:friction-26462",
        source_uri=f"cortex://notes/system/specs/{descriptive}",
        workspaces_root_path=tmp_path,
    )
    assert isinstance(prepared, GateDistillationInputs)
    assert prepared.spec_path == f"cortex://notes/system/specs/{descriptive}"
    assert prepared.path_resolution.basename_nonstandard is True
    assert prepared.path_resolution.action == "as_cited"


@pytest.mark.offline
def test_prepare_unreadable_names_cited_identity(
    cortex_root: Path,
    tmp_path: Path,
) -> None:
    _ = cortex_root
    result = prepare_gate_distillation(
        todo_id="todo:wire-gate",
        source_uri="cortex://notes/system/specs/typo-basename.md",
        workspaces_root_path=tmp_path,
    )
    assert isinstance(result, GateDistillationFailure)
    assert result.code == "implement_spec_unreadable"
    assert "typo-basename.md" in result.reason
    assert result.path_resolution is not None
    assert result.path_resolution.resolved == (
        "cortex://notes/system/specs/typo-basename.md"
    )


@pytest.mark.offline
@pytest.mark.parametrize(
    "source_uri",
    [
        "packet:universal-llm-gateway/tmp/prompts/foo.md",
        "agent-bus:2095",
    ],
)
def test_prepare_gate_distillation_rejects_non_spec_source_uri(
    cortex_root: Path,
    tmp_path: Path,
    source_uri: str,
) -> None:
    _write_cortex_spec(cortex_root, "wire-gate")

    result = prepare_gate_distillation(
        todo_id="todo:wire-gate",
        source_uri=source_uri,
        workspaces_root_path=tmp_path,
    )
    assert isinstance(result, GateDistillationFailure)
    assert result.code == "implement_spec_source_rejected"
    assert result.path_resolution is not None


@pytest.mark.offline
def test_build_implement_ready_evidence_uris() -> None:
    path = "cortex://notes/system/specs/wire-gate.md"
    uris = build_implement_ready_evidence_uris(path, _VALID_DENSE_SPEC)
    assert uris[0] == path
    assert uris[1] == dense_spec_hash_uri(_VALID_DENSE_SPEC)


@pytest.mark.offline
def test_read_dense_spec_text_from_cortex(cortex_root: Path) -> None:
    _write_cortex_spec(cortex_root, "wire-gate")

    text = read_dense_spec_text("cortex://notes/system/specs/wire-gate.md")
    assert text == _VALID_DENSE_SPEC


@pytest.mark.offline
def test_read_dense_spec_text_from_bare_notes_path(cortex_root: Path) -> None:
    """Bare notes/system/specs/... must read via cortex root (friction 23230)."""
    _write_cortex_spec(cortex_root, "wire-gate")

    text = read_dense_spec_text("notes/system/specs/wire-gate.md")
    assert text == _VALID_DENSE_SPEC


@pytest.mark.offline
def test_read_dense_spec_text_from_workspace(tmp_path: Path) -> None:
    _write_workspace_spec(tmp_path, "wire-gate")

    text = read_dense_spec_text(
        "tasks/specs/wire-gate.md",
        workspaces_root_path=tmp_path,
    )
    assert text == _VALID_DENSE_SPEC


@pytest.mark.offline
def test_prepare_gate_distillation_success_cortex(
    cortex_root: Path, tmp_path: Path
) -> None:
    _write_cortex_spec(cortex_root, "wire-gate")

    prepared = prepare_gate_distillation(
        todo_id="todo:wire-gate",
        workspaces_root_path=tmp_path,
    )
    assert isinstance(prepared, GateDistillationInputs)
    assert prepared.spec_path == "cortex://notes/system/specs/wire-gate.md"
    assert prepared.path_resolution.action == "defaulted_empty_source"
    assert prepared.evidence_uris[1] == dense_spec_hash_uri(_VALID_DENSE_SPEC)
    assert prepared.schema.passed is True


@pytest.mark.offline
def test_prepare_gate_distillation_success_with_cortex_source_uri(
    cortex_root: Path,
    tmp_path: Path,
) -> None:
    _write_cortex_spec(cortex_root, "wire-gate")

    prepared = prepare_gate_distillation(
        todo_id="todo:wire-gate",
        source_uri="cortex://notes/system/specs/wire-gate.md",
        workspaces_root_path=tmp_path,
    )
    assert isinstance(prepared, GateDistillationInputs)
    assert prepared.spec_path == "cortex://notes/system/specs/wire-gate.md"
    assert "workspaces://" not in prepared.spec_path


@pytest.mark.offline
def test_prepare_gate_distillation_rejects_missing_spec(
    cortex_root: Path,
    tmp_path: Path,
) -> None:
    _ = cortex_root
    _ = os.environ.get("CORTEX_FILES_ROOT")
    result = prepare_gate_distillation(
        todo_id="todo:missing",
        workspaces_root_path=tmp_path,
    )
    assert isinstance(result, GateDistillationFailure)
    assert result.code == "implement_spec_unreadable"


@pytest.mark.offline
def test_select_cited_dense_spec_uri_prefers_cortex_scheme() -> None:
    from implement_admission.implement_ready_gate_resolve import (
        select_cited_dense_spec_uri,
    )

    evidence = [
        "notes/system/specs/wire-gate.md",
        "cortex://notes/system/specs/wire-gate.md",
        "spec_sha256:abc",
    ]
    assert (
        select_cited_dense_spec_uri(
            evidence, source_uri="notes/system/specs/wire-gate.md"
        )
        == "cortex://notes/system/specs/wire-gate.md"
    )
