"""Birth-write timing tests (Shape A BW1–BW5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from implement_admission import closeout_helpers
from implement_admission.conductor_score_journal import read_tip
from implement_admission.source_ref import todo_slug_from_ref

from .route_conductor_commit import ConductorCommitPlan, commit_conductor_plan


@pytest.fixture
def cortex_files_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(closeout_helpers, "cortex_files_root", lambda: tmp_path)
    return tmp_path


@pytest.mark.offline
def test_giw_reject_leaves_no_birth_writes(
    tmp_path: Path, cortex_files_root: Path
) -> None:
    slug = "shape-a-bw1"
    source_ref = f"todo:{slug}"
    plan = ConductorCommitPlan(
        source_ref=source_ref,
        packet_markdown="---\nsource_ref: todo:shape-a-bw1\n---\n# x\n",
        packet_sha256="abc123",
        packet_rel_path=f"universal-llm-gateway/tmp/implement-admission/materialized/conductor-{slug}.md",
        birth_scoreboard_markdown="| ID | Deliverable | Mode | Status | Stops |\n",
        hop_seq=0,
    )
    out_dir = tmp_path / "tmp/implement-admission/materialized"
    out_dir.mkdir(parents=True)
    with pytest.raises(ValueError):
        commit_conductor_plan(plan, "", workspaces_root=tmp_path)
    assert read_tip(slug) is None
    assert not (out_dir / f"conductor-{slug}.md").exists()


@pytest.mark.offline
def test_commit_writes_packet_and_birth_tip(
    tmp_path: Path, cortex_files_root: Path
) -> None:
    slug = "shape-a-bw3"
    source_ref = f"todo:{slug}"
    body = (
        "---\nsource_ref: todo:shape-a-bw3\npacket_sha256: deadbeef\n---\n"
        "<task_guidance>\ncontract: conductor session\n</task_guidance>\n"
    )
    import hashlib

    digest = hashlib.sha256(body.encode()).hexdigest()
    plan = ConductorCommitPlan(
        source_ref=source_ref,
        packet_markdown=body,
        packet_sha256=digest,
        packet_rel_path=f"universal-llm-gateway/tmp/implement-admission/materialized/conductor-{slug}.md",
        birth_scoreboard_markdown=(
            "| ID | Deliverable | Mode | Status | Stops |\n"
            "|---|---|---|---|---|\n"
        ),
        hop_seq=0,
    )
    result = commit_conductor_plan(plan, "disp-bw3-test", workspaces_root=tmp_path)
    assert result.packet_sha256 == digest
    tip = read_tip(slug)
    assert tip is not None
    tip_body = tip[0] if isinstance(tip, tuple) else tip
    assert plan.birth_scoreboard_markdown.splitlines()[0] in tip_body


@pytest.mark.offline
def test_commit_idempotent(tmp_path: Path, cortex_files_root: Path) -> None:
    slug = "shape-a-bw4"
    source_ref = f"todo:{slug}"
    body = "---\nsource_ref: todo:shape-a-bw4\n---\n# same\n"
    import hashlib

    digest = hashlib.sha256(body.encode()).hexdigest()
    plan = ConductorCommitPlan(
        source_ref=source_ref,
        packet_markdown=body,
        packet_sha256=digest,
        packet_rel_path=f"universal-llm-gateway/tmp/implement-admission/materialized/conductor-{slug}.md",
        birth_scoreboard_markdown="| ID | Deliverable | Mode | Status | Stops |\n",
        hop_seq=0,
    )
    first = commit_conductor_plan(plan, "disp-1", workspaces_root=tmp_path)
    second = commit_conductor_plan(plan, "disp-2", workspaces_root=tmp_path)
    assert first.packet_sha256 == second.packet_sha256
    assert todo_slug_from_ref(source_ref) == slug
