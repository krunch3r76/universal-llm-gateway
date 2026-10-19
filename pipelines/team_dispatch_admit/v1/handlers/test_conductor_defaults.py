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
    """R1: prepare_conductor_packet → commit_conductor_plan; journal rows == [\"G1\"]."""
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
        "rows": ["G1"],
        "entry_gate": "G1",
        "stop_after": "G1",
    }

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
    assert wrap.birth_rows == ("G1",)
    assert wrap.birth_scoreboard_markdown is not None
    assert "| G1 |" in wrap.birth_scoreboard_markdown
    assert "| G7 |" not in wrap.birth_scoreboard_markdown

    commit_plan = ConductorCommitPlan(
        source_ref=source_ref,
        packet_markdown=wrap.packet_markdown or "",
        packet_sha256=str(wrap.packet_sha256 or ""),
        packet_rel_path=str(wrap.packet_rel_path or ""),
        birth_scoreboard_markdown=wrap.birth_scoreboard_markdown,
        hop_seq=0,
        birth_seed_ignored=bool(wrap.birth_seed_ignored),
        birth_rows=wrap.birth_rows or G_ROWS,
    )
    commit_conductor_plan(commit_plan, "disp-defaults-seed", workspaces_root=checkout)
    tip = read_tip(slug)
    assert tip is not None
    assert "**stop_after:** G1" in tip[0]
    journal = load_journal(slug)
    assert journal[0].get("rows") == ["G1"]


@pytest.mark.offline
def test_validate_and_defaults_accept_full_g_rows_seed() -> None:
    """Admit accepts full G_ROWS conductor seed without birth_seed_ignored."""
    conductor_opts = {
        "rows": list(G_ROWS),
        "entry_gate": "G1",
        "stop_after": "G1",
    }
    opts = {
        "op": "generate",
        "job": "conductor",
        "seat": "cursor-sdk",
        "hop_seq": 0,
        "source_ref": "todo:full-g-rows-seed",
        "generation_options": {"conductor": conductor_opts},
    }
    validate_out = evaluate_validate(opts)
    assert validate_out.get("admitted") is not False
    defaults_plan = evaluate_conductor_defaults(opts, validate_out)
    assert defaults_plan.get("birth_seed_ignored") is False


@pytest.mark.asyncio
@pytest.mark.offline
async def test_dispatch_cursor_sdk_generate_route_commits_birth_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1 route: GIW stub → commit path persists birth_rows [\"G1\"] in journal."""
    from implement_admission.test_conductor_materialize import _StubCortex
    from starlette.responses import Response
    from systems.frontier_consult.generate_wrap import (
        dispatch_cursor_sdk_generate_route,
    )
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    _patch_cortex_files_root(monkeypatch, tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(
        "systems.frontier_consult.handoff._workspaces_root",
        lambda: checkout,
    )

    class _FakeCortexReader:
        def __init__(self) -> None:
            self._stub = _StubCortex({})

        def entity_get(self, entity_id: str, **kwargs: object) -> dict:
            return self._stub.entity_get(entity_id, **kwargs)

        def list_relationships(
            self, entity_id: str, *, type_id: str | None = None
        ) -> list:
            return self._stub.list_relationships(entity_id, type_id=type_id)

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.StargateCortexReader",
        _FakeCortexReader,
    )

    async def _probe(_thread_id: str) -> dict[str, int]:
        return {"turn_count": 1}

    async def _prompt_resolution(**kwargs: object) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(
            text="",
            prompt_turn_number=None,
            prompt_bind_mode=None,
        )

    async def _threads(**kwargs: object) -> tuple[None, None, bool]:
        return None, None, False

    async def _generate(**kwargs: object) -> dict[str, str]:
        return {
            "dispatch_id": "disp-route-r1",
            "thread_id": "19999",
            "execution_id": "exec-r1",
            "status": "admitted",
        }

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.probe_thread",
        _probe,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_generate_prompt_resolution",
        _prompt_resolution,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_cursor_sdk_thread_targets",
        _threads,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        _generate,
    )

    slug = "route-seed-g1"
    conductor_opts = {
        "rows": ["G1"],
        "entry_gate": "G1",
        "stop_after": "G1",
    }
    body = TeamDispatchGenerateBody(
        op="generate",
        job="conductor",
        seat="cursor-sdk",
        source_ref=f"todo:{slug}",
        lane="B",
        caller_agent="cursor",
        dispatch_thread_id="19998",
        generation_options={"conductor": conductor_opts},
    )
    response = Response()
    result = await dispatch_cursor_sdk_generate_route(
        request_id="req-route-r1",
        body=body,
        seat="cursor-sdk",
        response=response,
    )
    assert isinstance(result, dict)
    assert result.get("dispatch_id") == "disp-route-r1"
    journal = load_journal(slug)
    assert journal[0].get("rows") == ["G1"]


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
