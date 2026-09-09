"""Hermetic tests for conductor packet materialization."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from implement_admission.conductor_materialize import (
    RematerializeContext,
    conductor_packet_contains_use_line,
    conductor_packet_has_lane_b,
    extract_scoreboard_uri,
    load_conductor_context,
    materialize_conductor,
    render_sparse_scoreboard,
    resolve_entry_gate,
)
from implement_admission.conductor_score_journal import (
    forward_mutate_tip,
    load_journal,
    read_tip,
    resolve_row_labels,
    resolve_scoreboard_rows,
    scoreboard_tip_uri,
    walk_journal_to_tip,
)
from implement_admission.conductor_summon import resolve_summon_mode
from implement_admission.conductor_witness import FoldDeps


class _StubCortex:
    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        self._attrs = attrs or {}

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        return {
            "id": entity_id,
            "name": "Layer conductor unify",
            "attributes": {
                "density_triage": "judgment_required",
                **self._attrs,
            },
        }

    def list_relationships(
        self,
        entity_id: str,
        *,
        type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = entity_id, type_id
        return []


def test_resolve_entry_gate_g1_without_fold() -> None:
    assert resolve_entry_gate(density_triage="judgment_required") == "G1"


def test_resolve_entry_gate_from_fold() -> None:
    assert resolve_entry_gate(density_triage="judgment_required", fold_entry_gate="G4") == "G4"


def test_resolve_entry_gate_g5_mechanical_without_fold() -> None:
    assert resolve_entry_gate(density_triage="mechanical") == "G5"


def test_resolve_entry_gate_derived_from_skips_g1() -> None:
    assert (
        resolve_entry_gate(
            density_triage="judgment_required",
            derived_from="document:harvest-architecture",
        )
        == "G2"
    )


def test_resolve_entry_gate_fold_wins_over_derived_from() -> None:
    assert (
        resolve_entry_gate(
            density_triage="judgment_required",
            derived_from="document:harvest-architecture",
            fold_entry_gate="G4",
        )
        == "G4"
    )


def test_sparse_scoreboard_seeds_witness_slots() -> None:
    body = render_sparse_scoreboard(
        source_ref="todo:foo",
        slug="foo",
        entry_gate="G1",
        stop_after=None,
    )
    for slot in ("F1", "S7", "S4b", "S9", "G4", "L1"):
        assert f"| {slot} | (pending) |" in body


def test_resolve_scoreboard_rows_from_acceptance_criteria() -> None:
    attrs = {
        "derived_from": "document:harvest-architecture",
        "acceptance_criteria": [
            "F1/F2 fix",
            "F3 fix",
            "pytest pass",
        ],
    }
    rows = resolve_scoreboard_rows(attrs)
    assert rows == ("R1", "R2", "R3")
    labels = resolve_row_labels(rows, attrs)
    assert labels["R1"] == "F1/F2 fix"
    assert labels["R3"] == "pytest pass"


def test_materialize_acceptance_criteria_rows_when_derived_from(
    tmp_path: Path,
) -> None:
    files_root = tmp_path / "cortex"
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:per-finding-rows",
        cortex=_StubCortex(
            {
                "derived_from": "document:8978-architecture",
                "acceptance_criteria": ["F1/F2", "F3", "pytest"],
            }
        ),
        out_dir=out_dir,
        files_root=files_root,
    )
    assert "Entry gate: R1" in mp.text
    assert "| R1 | F1/F2 | OPEN |" in mp.text
    assert "| R3 | pytest | OPEN |" in mp.text
    assert "| R1-BIND | (pending) |" in mp.text
    assert "| R1-LAND | (pending) |" in mp.text
    assert "| G1 |" not in mp.text
    tip = read_tip("per-finding-rows", files_root=files_root)
    assert tip is not None
    assert "**Entry gate:** R1" in tip[0]


def test_materialize_derived_from_entry_gate_and_invariants(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:derived-from-skip",
        cortex=_StubCortex({"derived_from": "document:8978-architecture"}),
        out_dir=out_dir,
        files_root=files_root,
    )
    assert "Entry gate: G2" in mp.text
    assert (
        "G1 CLOSED by derived_from:document:8978-architecture. "
        "Do not re-derive architecture."
    ) in mp.text
    tip = read_tip("derived-from-skip", files_root=files_root)
    assert tip is not None
    assert "**Entry gate:** G2" in tip[0]


def test_materialize_score_play_seat_language_no_tier_pointer(tmp_path: Path) -> None:
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=out_dir,
    )
    assert "cost tier" not in mp.text.lower()
    assert "tier table" not in mp.text.lower()
    assert "cursor-model-economics" not in mp.text
    assert "`cdp/opus-5`" in mp.text
    assert "`cdp/fable`" in mp.text
    assert "`OPEN FORK:`" in mp.text
    assert "score-play" in mp.text
    assert "runbook:score-play" not in mp.text  # inline URI, not a pointer phrase


def test_materialize_conductor_packet_shape(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=out_dir,
        files_root=files_root,
    )
    assert mp.packet_sha256
    assert "Use-line" not in mp.text  # guard against typo — real marker below
    assert conductor_packet_contains_use_line(mp.text)
    assert conductor_packet_has_lane_b(mp.text)
    assert "contract: conductor" in mp.text
    assert "work_key: todo:layer-conductor-unify" in mp.text
    frontmatter = mp.text.split("---")[1]
    assert "source_ref:" not in frontmatter
    assert not re.search(r"^todo:\s", frontmatter, re.MULTILINE)
    assert "| G1 |" in mp.text
    assert "| G6 |" in mp.text
    assert "| G7 |" in mp.text
    uri = extract_scoreboard_uri(mp.text)
    assert uri == scoreboard_tip_uri("layer-conductor-unify")
    tip = read_tip("layer-conductor-unify", files_root=files_root)
    assert tip is not None
    journal = load_journal("layer-conductor-unify", files_root=files_root)
    assert len(journal) == 1
    assert journal[0]["reason"] == "conductor spawn birth"
    assert walk_journal_to_tip("layer-conductor-unify", files_root=files_root) == tip[1]


def test_materialize_conductor_hop_contract(tmp_path: Path) -> None:
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=out_dir,
    )
    assert "Per-G-row hop (binding)" in mp.text
    assert "stop: ROW_HOP" in mp.text
    assert "422 CURSOR_WORKER_THREAD_OCCUPIED" in mp.text
    assert "stop: ROW_HOP | ROW_PINNED | HOLD_MERGE" in mp.text
    assert "hop_seq: <n>" in mp.text


def test_sparse_scoreboard_has_mode_column() -> None:
    body = render_sparse_scoreboard(
        source_ref="todo:foo",
        slug="foo",
        entry_gate="G1",
        stop_after=None,
    )
    assert "| Mode |" in body
    assert "| G3 | Densify | plan | OPEN |" in body
    assert "| G5 | Implement | agent | OPEN |" in body


def test_sparse_scoreboard_has_stops_column() -> None:
    ctx = load_conductor_context("todo:foo", cortex=_StubCortex())
    body = render_sparse_scoreboard(
        source_ref=ctx.source_ref,
        slug=ctx.slug,
        entry_gate=ctx.entry_gate,
        stop_after=ctx.stop_after,
    )
    assert "| Stops |" in body
    assert "stop_after" not in body or ctx.stop_after is None


def test_sparse_scoreboard_stop_after_when_set() -> None:
    ctx = load_conductor_context(
        "todo:foo",
        cortex=_StubCortex({"stop_after": "G1"}),
    )
    body = render_sparse_scoreboard(
        source_ref=ctx.source_ref,
        slug=ctx.slug,
        entry_gate=ctx.entry_gate,
        stop_after=ctx.stop_after,
    )
    assert "**stop_after:** G1" in body


def test_resolve_summon_mode_explicit_wins() -> None:
    assert (
        resolve_summon_mode(
            explicit="confer-and-finish",
            caller_agent="cursor",
            summon_text="anything",
        )
        == "confer_and_finish"
    )
    assert (
        resolve_summon_mode(
            explicit="attended",
            caller_agent="cursor-auto",
            summon_text="run with it",
        )
        == "attended"
    )


def test_resolve_summon_mode_caller_defaults() -> None:
    assert resolve_summon_mode(caller_agent="cursor") == "attended"
    assert resolve_summon_mode(caller_agent="cursor-ide") == "attended"
    assert resolve_summon_mode(caller_agent="cursor-auto") == "confer_and_finish"
    assert resolve_summon_mode(caller_agent=None) == "confer_and_finish"


def test_resolve_summon_mode_empty_coord_turn_count_zero() -> None:
    assert (
        resolve_summon_mode(caller_agent="cursor", summoning_turn_count=0)
        == "confer_and_finish"
    )
    assert (
        resolve_summon_mode(caller_agent="cursor", summoning_turn_count=3)
        == "attended"
    )
    assert (
        resolve_summon_mode(caller_agent="cursor", summoning_turn_count=None)
        == "attended"
    )


def test_resolve_summon_mode_confer_markers_in_text() -> None:
    assert (
        resolve_summon_mode(
            caller_agent="cursor",
            summon_text="please run with it on this",
        )
        == "confer_and_finish"
    )


def test_resolve_summon_mode_invalid_explicit_raises() -> None:
    with pytest.raises(ValueError, match="invalid summon_mode"):
        resolve_summon_mode(explicit="solo")


def test_todo_attr_summon_mode_overrides_attended_caller() -> None:
    ctx = load_conductor_context(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(attrs={"summon_mode": "confer_and_finish"}),
        caller_agent="cursor",
    )
    assert ctx.summon_mode == "confer_and_finish"


def test_materialize_conductor_attended_packet_strings(tmp_path: Path) -> None:
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=out_dir,
        caller_agent="cursor",
        summoning_thread_id="9638",
    )
    assert "summoning_thread_id: 9638" in mp.text
    assert "summon_mode: attended" in mp.text
    assert "SCORE_RESURFACE on summoning_thread_id=" in mp.text
    assert "never this worker thread" in mp.text
    assert (
        "Explicit see-score while attended: ROW_PINNED at G3, no pager"
        in mp.text
    )


def test_materialize_conductor_skips_birth_when_tip_exists(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    slug = "rematerialize-skip-test"
    source_ref = f"todo:{slug}"
    out_dir = tmp_path / "packets"
    materialize_conductor(
        source_ref,
        cortex=_StubCortex(),
        out_dir=out_dir,
        files_root=files_root,
    )
    tip_before = read_tip(slug, files_root=files_root)
    assert tip_before is not None
    g1_done_body = re.sub(
        r"(\|\s*G1\s*\|[^|]*\|)\s*OPEN\b",
        r"\1 DONE",
        tip_before[0],
        count=1,
        flags=re.IGNORECASE,
    )
    forward_mutate_tip(
        slug,
        next_body=g1_done_body,
        seat="conductor",
        dispatch_id="d1",
        reason="G1 harvest",
        rows=("G1",),
        delta="G1 OPEN→DONE",
        files_root=files_root,
    )
    tip_mutated = read_tip(slug, files_root=files_root)
    assert tip_mutated is not None
    journal_before = load_journal(slug, files_root=files_root)
    birth_count_before = sum(
        1 for record in journal_before if record.get("reason") == "conductor spawn birth"
    )
    assert birth_count_before == 1

    materialize_conductor(
        source_ref,
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets-rematerialize",
        files_root=files_root,
    )
    tip_after = read_tip(slug, files_root=files_root)
    assert tip_after is not None
    assert tip_after[0] == tip_mutated[0]
    assert tip_after[1] == tip_mutated[1]
    journal_after = load_journal(slug, files_root=files_root)
    birth_count_after = sum(
        1 for record in journal_after if record.get("reason") == "conductor spawn birth"
    )
    assert birth_count_after == 1
    assert len(journal_after) == len(journal_before)


def test_materialize_conductor_default_confer_and_finish_strings(tmp_path: Path) -> None:
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=out_dir,
    )
    assert "summon_mode: confer_and_finish" in mp.text
    assert (
        "G3→G5 default: in-process CDP score-ratify (do-not-fight / likely-optimal)."
        in mp.text
    )
    assert "Explicit see-score: ROW_PINNED at G3 + ping." in mp.text

    auto_mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets-auto",
        caller_agent="cursor-auto",
    )
    assert (
        "G3→G5 default: in-process CDP score-ratify (do-not-fight / likely-optimal)."
        in auto_mp.text
    )
    assert "Explicit see-score: ROW_PINNED at G3 + ping." in auto_mp.text


@pytest.mark.offline
def test_ac_p1_1_rematerialize_summoning_thread_scope(tmp_path: Path) -> None:
    """AC-P1-1 — rematerialize injects summoning_thread_id + SCORE_RESURFACE rule.

    Regression guard (A8): pre-change rematerialize omitted scoped summoning_thread_id
    when only the predecessor ledger carried it — scope showed worker thread only.
    """
    files_root = tmp_path / "cortex"
    slug = "rematerialize-summoning"
    source_ref = f"todo:{slug}"
    out_dir = tmp_path / "packets"
    materialize_conductor(
        source_ref,
        cortex=_StubCortex(),
        out_dir=out_dir,
        files_root=files_root,
        caller_agent="cursor",
    )
    rematerialize = RematerializeContext(summoning_thread_id="10223")
    mp = materialize_conductor(
        source_ref,
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets-remint",
        files_root=files_root,
        caller_agent="cursor",
        summoning_thread_id="10223",
        rematerialize=rematerialize,
    )
    assert "summoning_thread_id: 10223" in mp.text
    assert "SCORE_RESURFACE posts to that parent/root thread" in mp.text
    assert "never this worker thread" in mp.text


@pytest.mark.offline
def test_ac_p1_2_hop_fields_in_rendered_packet(tmp_path: Path) -> None:
    """AC-P1-2 — A5 RematerializeContext keys appear in rendered scope."""
    out_dir = tmp_path / "packets"
    rematerialize = RematerializeContext(
        hop_seq=3,
        predecessor_dispatch_id="pred-disp-9",
        scoreboard_tip_sha="abc123",
        scoreboard_entry_gate="G2",
        summoning_thread_id="9638",
    )
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=out_dir,
        caller_agent="cursor",
        summoning_thread_id="9638",
        rematerialize=rematerialize,
    )
    assert "hop_seq: 3 (hop metadata)" in mp.text
    assert "hop_from: pred-disp-9 (hop metadata)" in mp.text
    assert "scoreboard_tip_sha: abc123 (hop metadata)" in mp.text
    assert "scoreboard_entry_gate: G2 (hop metadata — not live entry_gate)" in mp.text
    assert "Entry gate: G1" in mp.text


@pytest.mark.offline
def test_ac_p1_3_rematerialize_entry_gate_from_fold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-P1-3 — live fold entry_gate on rematerialize, not sparse G1 default.

    Regression guard (A8): pre-change rematerialize rewound entry_gate to G1 despite
    witness fold resolving G3 on an existing tip.
    """
    from implement_admission.conductor_witness_types import FoldResult

    class _FoldBus:
        def has_score_resurface_after(self, **kwargs: object) -> bool:
            return False

        def nested_implement_has_commits(self, **kwargs: object) -> bool:
            return False

    class _FoldGit:
        def is_ancestor(self, commit: str, ref: str) -> bool:
            return False

    def _fold_g3(*_args: object, **_kwargs: object) -> FoldResult:
        return FoldResult(
            slug="entry-gate-fold",
            raw_body="",
            folded_body="",
            row_status={"G1": "DONE", "G2": "DONE", "G3": "OPEN"},
            witnesses={},
            witnessed_done=frozenset({"G1", "G2"}),
            rows_claimed=frozenset(),
            entry_gate="G3",
            missing_witnesses={},
        )

    monkeypatch.setattr(
        "implement_admission.conductor_materialize.fold_scoreboard",
        _fold_g3,
    )

    files_root = tmp_path / "cortex"
    repo = tmp_path / "repo"
    repo.mkdir()
    slug = "entry-gate-fold"
    source_ref = f"todo:{slug}"
    materialize_conductor(
        source_ref,
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets-birth",
        files_root=files_root,
    )
    fold_deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_FoldBus(),
        git=_FoldGit(),
        source_ref=source_ref,
        repo=repo,
    )
    mp = materialize_conductor(
        source_ref,
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets-remint",
        files_root=files_root,
        fold_deps=fold_deps,
    )
    assert "Entry gate: G3" in mp.text
    assert "Resume at persisted row (entry gate G3)" in mp.text
