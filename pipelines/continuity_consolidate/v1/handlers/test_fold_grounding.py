"""Friction 37643 — house mission must not ride an unrelated CLOSEOUT's URI."""

from __future__ import annotations

import pytest

from pipelines.continuity_consolidate.v1.handlers._grounding import (
    _MIN_GROUND,
    closeout_source_text,
    fold_evidence_uris,
    grounded_in_closeout,
    partial_card_description,
    resume_evidence_uris,
    select_mission,
    select_resume,
    stamp_watermark_on_description,
)

pytestmark = pytest.mark.offline

_STALE_MISSION = (
    "Harvest CONSULT_PENDING tokens to seat the operator-ear on Plane A, "
    "resolving credential divergence between model pools to enable pipeline continuity."
)
_STALE_NEXT = (
    "Restore paid plan on crsr_fb account OR repoint CURSOR_API_KEY to working "
    "crsr_3c value and sync restart git_integration_worker service."
)
_CLOSEOUT = (
    "Design: cortex://notes/system/threads/14759-rag-search-design.md. "
    "Bind: pipelines/rag/v2/, id stays rag-context (version 3.0); 14 named steps."
)


def test_stale_hub_mission_is_not_grounded_in_unrelated_closeout():
    sources = closeout_source_text(
        {"subject": "CLOSEOUT: rag search", "body": _CLOSEOUT},
        {},
    )
    assert not grounded_in_closeout(_STALE_MISSION, sources)
    text, source = select_mission(
        tip_residue=None, fold_mission=_STALE_MISSION, sources=sources
    )
    assert text == ""
    assert source == "none"


def test_fold_mission_kept_when_closeout_states_it():
    mission = "Ship rag-search v2 with YAML decision tables as the map."
    sources = closeout_source_text({"body": f"Next: {mission} Design only."}, {})
    text, source = select_mission(
        tip_residue=None, fold_mission=mission, sources=sources
    )
    assert text == mission
    assert source == "closeout"


def test_checkpoint_quote_wins_over_fold():
    residue = "Mission: Run the pipeline track (CURRENT LEG 43).\nNext: harvest."
    text, source = select_mission(
        tip_residue=residue,
        fold_mission=_STALE_MISSION,
        sources=closeout_source_text({"body": _CLOSEOUT}, {"residue": residue}),
    )
    assert text == "Run the pipeline track (CURRENT LEG 43)."
    assert source == "checkpoint"


def test_stale_resume_fields_dropped():
    kept = select_resume(
        {"settled": _STALE_NEXT, "live": "in flight", "next": _STALE_NEXT},
        closeout_source_text({"body": _CLOSEOUT}, {}),
    )
    assert kept == {}


def test_grounded_resume_field_kept():
    next_line = "Seed todo:rag-search-pipeline-v2 after the design closeout."
    kept = select_resume(
        {"next": next_line, "settled": _STALE_NEXT},
        closeout_source_text({"body": f"Plan: {next_line}"}, {}),
    )
    assert kept == {"next": next_line}


def test_evidence_uris_do_not_default_to_trigger_for_ungrounded_text():
    uris = fold_evidence_uris(
        text=_STALE_MISSION,
        trigger_ref="agent-bus:14759#8",
        trigger_body=_CLOSEOUT,
        tip_ref="agent-bus:12286#138",
        tip_residue="Mission: resume continuity house from this CHECKPOINT.",
        quoted=False,
    )
    assert uris == []


def test_evidence_uris_cite_trigger_when_body_holds_text():
    text = "14 named steps in a generic decision_table_v1"
    uris = fold_evidence_uris(
        text=text,
        trigger_ref="agent-bus:14759#8",
        trigger_body=_CLOSEOUT + " " + text,
        tip_ref=None,
        tip_residue="",
        quoted=False,
    )
    assert uris == ["agent-bus:14759#8"]


def test_prefix_match_with_hub_tail_not_grounded():
    mixed = (
        "Bind: pipelines/rag/v2/, id stays rag-context "
        "and restore paid plan on crsr_fb account."
    )
    needle = " ".join(mixed.lower().split())
    hay = " ".join(_CLOSEOUT.lower().split())
    assert needle[:_MIN_GROUND] in hay
    assert needle not in hay
    assert not grounded_in_closeout(mixed, _CLOSEOUT)
    uris = fold_evidence_uris(
        text=mixed,
        trigger_ref="agent-bus:14759#8",
        trigger_body=_CLOSEOUT,
        tip_ref=None,
        tip_residue="",
        quoted=False,
    )
    assert uris == []


def test_resume_uris_union_per_field():
    settled = "Bind: pipelines/rag/v2/, id stays rag-context for this house."
    next_line = "Seed todo:rag-search-pipeline-v2 after the design closeout lands."
    uris = resume_evidence_uris(
        resume={"settled": settled, "next": next_line},
        trigger_ref="agent-bus:14759#8",
        trigger_body=f"Done. {settled}",
        tip_ref="agent-bus:12286#200",
        tip_residue=f"Next: {next_line}",
    )
    assert uris == ["agent-bus:14759#8", "agent-bus:12286#200"]
    joined = fold_evidence_uris(
        text=f"{settled} {next_line}",
        trigger_ref="agent-bus:14759#8",
        trigger_body=f"Done. {settled}",
        tip_ref="agent-bus:12286#200",
        tip_residue=f"Next: {next_line}",
        quoted=False,
    )
    assert joined == []


def test_watermark_stamp_keeps_prior_consolidated_through():
    prior = (
        "Mission: Harvest CONSULT_PENDING tokens. "
        "Next: Restore paid plan on crsr_fb. "
        "Consolidated through agent-bus:12286#1308 at 2026-09-30T06:41:29Z "
        "(consolidate-continuity v1)."
    )
    out = stamp_watermark_on_description(
        prior, "agent-bus:14759#8", "2026-10-03T06:40:19Z"
    )
    assert "Harvest CONSULT_PENDING" in out
    assert "Consolidated through agent-bus:12286#1308" in out
    assert "Consolidated through agent-bus:14759#8" not in out
    assert (
        "Last fold agent-bus:14759#8 at 2026-10-03T06:40:19Z: "
        "no grounded mission/resume."
    ) in out


def test_partial_resume_does_not_blank_mission():
    prior = (
        "Mission: Harvest CONSULT_PENDING tokens. "
        "Next: Restore paid plan on crsr_fb. "
        "Consolidated through agent-bus:12286#1308 at 2026-09-30T06:41:29Z "
        "(consolidate-continuity v1)."
    )
    grounded_next = "Seed todo:rag-search-pipeline-v2 after the design closeout."
    out = partial_card_description(
        prior,
        {"next": grounded_next},
        "agent-bus:14759#8",
        "2026-10-03T06:40:19Z",
    )
    assert "Harvest CONSULT_PENDING" in out
    assert "(none folded yet)" not in out
    assert grounded_next in out
    assert "Consolidated through agent-bus:12286#1308" in out
    assert "Consolidated through agent-bus:14759#8" not in out
    assert "Resume fold agent-bus:14759#8 at 2026-10-03T06:40:19Z." in out
