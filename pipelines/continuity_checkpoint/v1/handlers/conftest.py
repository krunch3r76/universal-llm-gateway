"""Shared fixtures for continuity_checkpoint handler tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from implement_admission.conductor_score_journal import birth_scoreboard
from implement_admission.conductor_witness import FoldDeps

DISTINCT_MODE = "agent-special"


class StubCortex:
    def __init__(
        self,
        *,
        attrs: dict[str, Any] | None = None,
        relationships: list[dict[str, Any]] | None = None,
    ) -> None:
        self._attrs = attrs or {"density_triage": "mechanical"}
        self._relationships = relationships or []

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        if entity_id.startswith("document:"):
            return {"id": entity_id, "attributes": {"consult_kind": "architecture"}}
        return {"id": entity_id, "attributes": dict(self._attrs)}

    def list_relationships(
        self,
        entity_id: str,
        *,
        type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = entity_id, type_id
        return list(self._relationships)


class StubBus:
    def has_score_resurface_after(self, *, thread_id: str, after_written_at: str | None) -> bool:  # noqa: ARG002
        return False


class StubGit:
    def is_ancestor(self, commit: str, ref: str) -> bool:  # noqa: ARG002
        return False


def g1_witness_deps(slug: str, repo: Path) -> FoldDeps:
    rel = {
        "id": 42,
        "source_id": f"todo:{slug}",
        "target_id": f"document:{slug}-architecture",
        "type_id": "derived_from",
    }
    return FoldDeps(
        cortex=StubCortex(relationships=[rel]),
        bus=StubBus(),
        git=StubGit(),
        source_ref=f"todo:{slug}",
        repo=repo,
    )


def charter_board(files_root: Path, thread: str) -> str:
    uri = f"cortex://notes/system/threads/{thread}-charter-scoreboard.md"
    path = files_root / uri.removeprefix("cortex://")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Charter\n\n## Rows\n\n| R1 | item | todo:a | OPEN | |\n",
        encoding="utf-8",
    )
    return uri


def conductor_board(
    files_root: Path,
    slug: str,
    *,
    mode: str = DISTINCT_MODE,
) -> str:
    body = "\n".join(
        [
            "# Scoreboard",
            "",
            "## Gated deliverables",
            "",
            "| ID | Deliverable | Mode | Status | Stops |",
            "|---|---|---|---|---|",
            f"| G1 | Architecture | {mode} | OPEN | |",
        ]
    )
    birth_scoreboard(slug, scoreboard_body=body, files_root=files_root)
    return f"cortex://notes/system/scoreboards/{slug}-scoreboard.md"


def continuity_card(files_root: Path, thread: str) -> Path:
    card = files_root / "notes/system/threads" / f"{thread}-continuity.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text("# card\n", encoding="utf-8")
    return card


@pytest.fixture
def cortex_files_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    return tmp_path
