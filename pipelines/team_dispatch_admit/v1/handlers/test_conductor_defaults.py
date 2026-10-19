"""Shape A AC4 — hop-class seat selection in conductor defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
from implement_admission import closeout_helpers, conductor_score_locus
from implement_admission.conductor_policy_snapshot import (
    default_knobs_from_route_policy,
)
from implement_admission.conductor_score_journal import (
    G_ROWS,
    load_journal,
    read_tip,
    render_sparse_scoreboard,
)

from .conductor_defaults import evaluate_conductor_defaults
from .validate import evaluate_validate


def _seed_tip(tmp_path: Path, slug: str) -> Path:
    body = render_sparse_scoreboard(
        source_ref=f"todo:{slug}",
        slug=slug,
        entry_gate="G2",
        stop_after=None,
        rows=("G1", "G2", "G3"),
    )
    tip_path = tmp_path / "notes" / "system" / "scoreboards" / f"{slug}-scoreboard.md"
    tip_path.parent.mkdir(parents=True, exist_ok=True)
    tip_path.write_text(body, encoding="utf-8")
    return tmp_path


def _run(opts: dict) -> dict:
    v = evaluate_validate(opts)
    return evaluate_conductor_defaults(opts, v)


def _patch_cortex_files_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(closeout_helpers, "cortex_files_root", lambda: root)
    monkeypatch.setattr(conductor_score_locus, "cortex_files_root", lambda: root)


@pytest.mark.parametrize(
    "verb",
    ["reply-fold", "child-recheck", "harvest", "fold"],
)
@pytest.mark.offline
def test_mechanical_verbs(verb: str, tmp_path: Path) -> None:
    snap = default_knobs_from_route_policy()
    root = _seed_tip(tmp_path, "ac4-mechanical")
    plan = _run(
        {
            "op": "generate",
            "job": "conductor",
            "seat": "cursor-sdk",
            "hop_seq": 3,
            "next_admit_verb": verb,
            "source_ref": "todo:ac4-mechanical",
            "files_root": str(root),
            "_test_todo_attrs": {"name": "ac4"},
            "_skip_packet_render": True,
        }
    )
    assert plan.get("selected_seat_role") == "mechanical"
    assert plan.get("model") == snap.mechanical["model"]


@pytest.mark.offline
def test_judgment_and_birth_use_driver(tmp_path: Path) -> None:
    snap = default_knobs_from_route_policy()
    slug = "ac4-driver"
    root = _seed_tip(tmp_path, slug)
    cases = (
        {"hop_seq": 0, "next_admit_verb": None, "files_root": "/tmp"},
        {"hop_seq": 2, "next_admit_verb": "judgment", "files_root": str(root)},
    )
    for extra in cases:
        plan = _run(
            {
                "op": "generate",
                "job": "conductor",
                "seat": "cursor-sdk",
                "source_ref": f"todo:{slug}",
                "_test_todo_attrs": {"name": "ac4"},
                "_skip_packet_render": True,
                **extra,
            }
        )
        assert plan.get("selected_seat_role") == "driver"
        assert plan.get("model") == snap.driver["model"]


@pytest.mark.offline
def test_omitted_files_root_uses_cortex_not_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: omitted files_root must resolve tips via cortex_files_root(), not /tmp."""
    cortex_path = tmp_path / "cortex"
    _patch_cortex_files_root(monkeypatch, cortex_path)
    slug = "f1-omit-files-root"
    _seed_tip(cortex_path, slug)
    assert read_tip(slug, files_root=Path("/tmp")) is None

    opts = {
        "op": "generate",
        "job": "conductor",
        "seat": "cursor-sdk",
        "hop_seq": 1,
        "source_ref": f"todo:{slug}",
    }
    validate_out = evaluate_validate(opts)
    assert validate_out.get("code") != "conductor_tip_required"
    assert validate_out.get("admitted") is not False
    plan = evaluate_conductor_defaults(opts, validate_out)
    assert plan.get("admitted") is not False
    assert plan.get("birth_scoreboard_markdown") is None


@pytest.mark.offline
def test_todo_source_ref_defers_packet_render_to_enrich() -> None:
    """F2: admit defaults must not stub-render packet_markdown from entity name 'test'."""
    plan = _run(
        {
            "op": "generate",
            "job": "conductor",
            "seat": "cursor-sdk",
            "hop_seq": 0,
            "source_ref": "todo:defer-enrich-packet",
        }
    )
    assert plan.get("admitted") is not False
    assert plan.get("packet_markdown") == ""
    assert plan.get("packet_sha256") == ""
    assert plan.get("packet_rel_path") == ""
    assert "test" not in (plan.get("packet_markdown") or "").lower()


@pytest.mark.offline
def test_birth_seed_rows_through_prepare_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: prepare_conductor_packet → commit_conductor_plan with full G-ladder seeds."""
    from implement_admission.test_conductor_materialize import _StubCortex
    from systems.frontier_consult.generate_wrap import prepare_conductor_packet
    from systems.frontier_consult.route_conductor_commit import (
        ConductorCommitPlan,
        commit_conductor_plan,
    )

    _patch_cortex_files_root(monkeypatch, tmp_path)
    slug = "defaults-seed-g1"
    source_ref = f"todo:{slug}"
    conductor_opts = {
        "rows": list(G_ROWS),
        "entry_gate": "G1",
        "stop_after": "G1",
    }
    validate_out = evaluate_validate(
        {
            "op": "generate",
            "job": "conductor",
            "seat": "cursor-sdk",
            "hop_seq": 0,
            "source_ref": source_ref,
            "generation_options": {"conductor": conductor_opts},
        }
    )
    assert validate_out.get("admitted") is not False
    defaults_plan = evaluate_conductor_defaults(
        {
            "op": "generate",
            "job": "conductor",
            "seat": "cursor-sdk",
            "hop_seq": 0,
            "source_ref": source_ref,
            "generation_options": {"conductor": conductor_opts},
        },
        validate_out,
    )
    assert defaults_plan.get("birth_seed_ignored") is False

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    wrap = prepare_conductor_packet(
        request_id="req-r1",
        source_ref=source_ref,
        caller_agent="cursor",
        cortex=_StubCortex({}),
        workspaces_root=checkout,
        conductor_opts=conductor_opts,
    )
    assert wrap.materialized
    assert wrap.birth_rows == G_ROWS
    assert wrap.birth_scoreboard_markdown is not None
    assert "| G7 |" in wrap.birth_scoreboard_markdown

    commit_plan = ConductorCommitPlan(
        source_ref=source_ref,
        packet_markdown=wrap.packet_markdown or "",
        packet_sha256=str(wrap.packet_sha256 or ""),
        packet_rel_path=str(wrap.packet_rel_path or ""),
        birth_scoreboard_markdown=wrap.birth_scoreboard_markdown,
        hop_seq=0,
        birth_seed_ignored=bool(
            defaults_plan.get("birth_seed_ignored") or wrap.birth_seed_ignored
        ),
        birth_rows=wrap.birth_rows or G_ROWS,
    )
    commit_conductor_plan(commit_plan, "disp-defaults-seed", workspaces_root=checkout)
    tip = read_tip(slug)
    assert tip is not None
    assert "**stop_after:** G1" in tip[0]
    journal = load_journal(slug)
    assert journal[0].get("rows") == list(G_ROWS)


@pytest.mark.offline
def test_prepare_conductor_packet_hop1_uses_cortex_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1 hop-1: scoreboard tip read from cortex_files_root, not checkout repo."""
    from implement_admission.conductor_context import RematerializeContext
    from implement_admission.test_conductor_materialize import _StubCortex
    from systems.frontier_consult.generate_wrap import prepare_conductor_packet

    cortex_root = tmp_path / "cortex"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _patch_cortex_files_root(monkeypatch, cortex_root)
    slug = "defaults-hop1-tip"
    source_ref = f"todo:{slug}"
    tip_body = render_sparse_scoreboard(
        source_ref=source_ref,
        slug=slug,
        entry_gate="G2",
        stop_after=None,
        rows=G_ROWS,
    )
    tip_path = (
        cortex_root / "notes" / "system" / "scoreboards" / f"{slug}-scoreboard.md"
    )
    tip_path.parent.mkdir(parents=True, exist_ok=True)
    tip_path.write_text(tip_body, encoding="utf-8")
    assert read_tip(slug, files_root=checkout) is None

    wrap = prepare_conductor_packet(
        request_id="req-hop1",
        source_ref=source_ref,
        caller_agent="cursor",
        cortex=_StubCortex({}),
        workspaces_root=checkout,
        rematerialize=RematerializeContext(hop_seq=1),
    )
    text = wrap.packet_markdown or ""
    assert "## Gated deliverables (open)" in text
    assert "Do not fs-read this tip" in text


@pytest.mark.parametrize(
    "conductor_opts,field",
    [
        ({"rows": ["G4", "G5", "G6", "G7"]}, "conductor.rows"),
        ({"rows": ["G1"]}, "conductor.rows"),
        ({"rows": ["BAD_ROW"]}, "conductor.rows"),
        ({"rows": list(G_ROWS), "entry_gate": "G9"}, "conductor.entry_gate"),
    ],
)
@pytest.mark.offline
def test_validate_rejects_invalid_conductor_seeds(
    conductor_opts: dict, field: str
) -> None:
    """R3: admit rejects partial G ladder, bad row ids, and entry_gate ∉ rows."""
    out = evaluate_validate(
        {
            "op": "generate",
            "job": "conductor",
            "seat": "cursor-sdk",
            "hop_seq": 0,
            "source_ref": "todo:reject-seed",
            "generation_options": {"conductor": conductor_opts},
        }
    )
    assert out.get("admitted") is False
    assert out.get("field") == field
    assert out.get("code") == "conductor_seed_invalid"


@pytest.mark.offline
def test_birth_seed_ignored_when_tip_present_at_hop_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: hop_seq=0 with existing tip ignores conductor seed knobs."""
    slug = "defaults-tip-ignores-seed"
    _patch_cortex_files_root(monkeypatch, tmp_path)
    _seed_tip(tmp_path, slug)
    conductor_opts = {
        "rows": list(G_ROWS),
        "entry_gate": "G1",
        "stop_after": "G1",
    }
    validate_out = evaluate_validate(
        {
            "op": "generate",
            "job": "conductor",
            "seat": "cursor-sdk",
            "hop_seq": 0,
            "source_ref": f"todo:{slug}",
            "generation_options": {"conductor": conductor_opts},
            "files_root": str(tmp_path),
        }
    )
    assert validate_out.get("admitted") is not False
    plan = evaluate_conductor_defaults(
        {
            "op": "generate",
            "job": "conductor",
            "seat": "cursor-sdk",
            "hop_seq": 0,
            "source_ref": f"todo:{slug}",
            "generation_options": {"conductor": conductor_opts},
            "files_root": str(tmp_path),
        },
        validate_out,
    )
    assert plan.get("birth_seed_ignored") is True
