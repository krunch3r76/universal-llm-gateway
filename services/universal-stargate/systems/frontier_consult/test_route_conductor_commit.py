"""Birth-write timing tests (Shape A BW1–BW5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from implement_admission import closeout_helpers, conductor_score_locus
from implement_admission.conductor_score_journal import (
    read_tip,
    render_sparse_scoreboard,
)
from implement_admission.source_ref import todo_slug_from_ref

from .route_conductor_commit import ConductorCommitPlan, commit_conductor_plan


def _seed_tip(files_root: Path, slug: str) -> None:
    body = render_sparse_scoreboard(
        source_ref=f"todo:{slug}",
        slug=slug,
        entry_gate="G2",
        stop_after=None,
        rows=("G1", "G2", "G3"),
    )
    tip_path = files_root / "notes" / "system" / "scoreboards" / f"{slug}-scoreboard.md"
    tip_path.parent.mkdir(parents=True, exist_ok=True)
    tip_path.write_text(body, encoding="utf-8")


@pytest.fixture
def cortex_files_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(closeout_helpers, "cortex_files_root", lambda: tmp_path)
    monkeypatch.setattr(conductor_score_locus, "cortex_files_root", lambda: tmp_path)
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


@pytest.mark.offline
def test_birth_tip_on_cortex_distinct_from_checkout_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3: scoreboard birth uses cortex_files_root; packet file uses checkout root."""
    cortex_root = tmp_path / "cortex"
    checkout_root = tmp_path / "checkout"
    checkout_root.mkdir()
    monkeypatch.setattr(closeout_helpers, "cortex_files_root", lambda: cortex_root)
    monkeypatch.setattr(conductor_score_locus, "cortex_files_root", lambda: cortex_root)

    slug = "f3-cortex-vs-checkout"
    source_ref = f"todo:{slug}"
    body = "---\nsource_ref: todo:f3-cortex-vs-checkout\n---\n# packet\n"
    import hashlib

    digest = hashlib.sha256(body.encode()).hexdigest()
    plan = ConductorCommitPlan(
        source_ref=source_ref,
        packet_markdown=body,
        packet_sha256=digest,
        packet_rel_path=(
            "universal-llm-gateway/tmp/implement-admission/materialized/"
            f"conductor-{slug}.md"
        ),
        birth_scoreboard_markdown="| ID | Deliverable | Mode | Status | Stops |\n",
        hop_seq=0,
    )
    commit_conductor_plan(plan, "disp-f3", workspaces_root=checkout_root)

    packet_path = (
        checkout_root / "tmp/implement-admission/materialized" / f"conductor-{slug}.md"
    )
    assert packet_path.is_file()
    assert read_tip(slug) is not None
    assert read_tip(slug, files_root=checkout_root) is None

    decoy_slug = "f3-checkout-only-tip"
    _seed_tip(checkout_root, decoy_slug)
    assert read_tip(decoy_slug) is None
    assert read_tip(decoy_slug, files_root=checkout_root) is not None
