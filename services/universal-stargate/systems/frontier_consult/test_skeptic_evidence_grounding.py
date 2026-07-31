"""Unit tests for skeptic FILE_EVIDENCE_PATHS extraction and grounding."""

from __future__ import annotations

from pathlib import Path

import pytest

from systems.frontier_consult.skeptic_evidence_grounding import (
    evaluate_skeptic_evidence_grounding,
    parse_skeptic_reply_evidence,
)

_SPEC = "tasks/specs/boot-card-platform-skill-delivery.md"
_WORKSPACES_URI = f"workspaces://universal-llm-gateway/{_SPEC}"


class _FakeBusReader:
    def __init__(self, *, body: str) -> None:
        self._body = body

    def bus_turn_get(self, thread: str, turn_number: int) -> dict[str, object] | None:
        return {"body": self._body}

    def bus_thread_last_turn(self, thread: str) -> dict[str, object] | None:
        return None


def _bulleted_body(*paths: str) -> str:
    lines = ["RATIFY", "", "FILE_EVIDENCE_PATHS:"]
    lines.extend(f"- {path}" for path in paths)
    return "\n".join(lines)


@pytest.fixture()
def workspaces_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    spec_dir = tmp_path / "universal-llm-gateway" / "tasks" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "boot-card-platform-skill-delivery.md").write_text(
        "# Dense spec\n", encoding="utf-8"
    )
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    return tmp_path


@pytest.mark.offline
def test_parse_skeptic_reply_evidence_strips_markdown_bullets() -> None:
    body = _bulleted_body(_WORKSPACES_URI, _SPEC)
    paths, grounding_mode, malformed = parse_skeptic_reply_evidence(body)

    assert paths == [_WORKSPACES_URI, _SPEC]
    assert all(not path.startswith("- ") for path in paths)
    assert grounding_mode is None
    assert not malformed


@pytest.mark.offline
def test_parse_skeptic_reply_evidence_accepts_bare_paths() -> None:
    body = "\n".join(
        [
            "RATIFY",
            "",
            "FILE_EVIDENCE_PATHS:",
            _WORKSPACES_URI,
            _SPEC,
        ]
    )
    paths, _, malformed = parse_skeptic_reply_evidence(body)

    assert paths == [_WORKSPACES_URI, _SPEC]
    assert not malformed


@pytest.mark.offline
def test_bulleted_paths_ground_under_temp_workspaces_root(
    workspaces_root: Path,
) -> None:
    body = _bulleted_body(_WORKSPACES_URI, _SPEC)
    assertion = {
        "observed_at": "2026-06-29T12:00:00+00:00",
        "evidence_uris": ["agent-bus:4288#turn-12"],
    }
    outcome = evaluate_skeptic_evidence_grounding(
        reader=_FakeBusReader(body=body),
        assertion=assertion,
        workspaces_root=workspaces_root,
    )

    assert outcome.grounded is True
    assert outcome.unresolved is None
    assert outcome.mode is None


@pytest.mark.offline
def test_parse_skeptic_reply_evidence_strips_symbol_annotation() -> None:
    annotated = (
        f"{_WORKSPACES_URI} :: SomeSymbol (~L42) — grounds the implement gate"
    )
    body = _bulleted_body(annotated)
    paths, _, malformed = parse_skeptic_reply_evidence(body)

    assert paths == [_WORKSPACES_URI]
    assert not malformed


@pytest.mark.offline
def test_annotated_paths_ground_without_oserror(workspaces_root: Path) -> None:
    annotated = (
        f"{_WORKSPACES_URI} :: SomeSymbol (~L42) — grounds the implement gate"
    )
    body = _bulleted_body(annotated)
    assertion = {
        "observed_at": "2026-06-29T12:00:00+00:00",
        "evidence_uris": ["agent-bus:4421#turn-3"],
    }
    outcome = evaluate_skeptic_evidence_grounding(
        reader=_FakeBusReader(body=body),
        assertion=assertion,
        workspaces_root=workspaces_root,
    )

    assert outcome.grounded is True


@pytest.mark.offline
def test_ground_skeptic_file_paths_oserror_is_unresolved_not_crash() -> None:
    from systems.frontier_consult.skeptic_evidence_grounding import (
        ground_skeptic_file_paths,
    )

    long_component = "a" * 300 + ".py"
    path = f"workspaces://universal-llm-gateway/libs/{long_component}"
    unresolved, resolved = ground_skeptic_file_paths(
        [path],
        workspaces_root=Path("/mnt/torus/projects"),
    )

    assert unresolved == [path]
    assert resolved == 0


@pytest.mark.offline
def test_malformed_unknown_scheme_returns_mode_malformed() -> None:
    body = _bulleted_body("notfs://universal-llm-gateway/tasks/specs/fake.md")
    paths, _, malformed = parse_skeptic_reply_evidence(body)

    assert paths == []
    assert malformed


@pytest.mark.offline
def test_missing_file_returns_unresolved(workspaces_root: Path) -> None:
    body = _bulleted_body("tasks/specs/does-not-exist.md")
    assertion = {
        "observed_at": "2026-06-29T12:00:00+00:00",
        "evidence_uris": ["agent-bus:4288#turn-12"],
    }
    outcome = evaluate_skeptic_evidence_grounding(
        reader=_FakeBusReader(body=body),
        assertion=assertion,
        workspaces_root=workspaces_root,
    )

    assert outcome.grounded is False
    assert outcome.unresolved == ["tasks/specs/does-not-exist.md"]


@pytest.mark.offline
def test_role_labeled_bridge_body_uses_same_evidence_grammar(
    workspaces_root: Path,
) -> None:
    """Cursor-path role bridge must satisfy the same gate grammar as API on-behalf."""
    from systems.frontier_consult.cursor_sdk_role_delivery import (
        build_role_labeled_turn_body,
    )

    body = build_role_labeled_turn_body(
        "RATIFY",
        [_WORKSPACES_URI],
    )
    paths, _mode, malformed = parse_skeptic_reply_evidence(body)
    assert not malformed
    assert paths == [_WORKSPACES_URI]
    outcome = evaluate_skeptic_evidence_grounding(
        reader=_FakeBusReader(body=body),
        assertion={
            "observed_at": "2026-06-29T12:00:00+00:00",
            "evidence_uris": ["agent-bus:4288#turn-12"],
        },
        workspaces_root=workspaces_root,
    )
    assert outcome.grounded is True
