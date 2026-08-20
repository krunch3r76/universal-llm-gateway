"""Unit tests for Gate-2 implement-admission distillation helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.gate_distillation import (
    build_implement_ready_evidence_uris,
    default_dense_spec_uri,
    normalize_dense_spec_path,
    prepare_gate_distillation,
    read_dense_spec_text,
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
def test_normalize_dense_spec_path_ignores_non_matching_basename() -> None:
    assert (
        normalize_dense_spec_path(
            "workspaces://universal-llm-gateway/tasks/specs/custom.md",
            todo_id="todo:wire-gate",
        )
        == "cortex://notes/system/specs/wire-gate.md"
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
    assert isinstance(result, tuple)
    code, _reason = result
    assert code == "implement_spec_source_rejected"


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
    assert not isinstance(prepared, tuple)
    assert prepared.spec_path == "cortex://notes/system/specs/wire-gate.md"
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
    assert not isinstance(prepared, tuple)
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
    assert isinstance(result, tuple)
    code, _reason = result
    assert code == "implement_spec_unreadable"


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
