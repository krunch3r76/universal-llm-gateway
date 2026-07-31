"""Acceptance tests for stale-prose scanner (todo:stale-prose-scanner)."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cortex_store.prose_fact_scan.constants import REPORT_DIR, TIER_A_GLOBS
from cortex_store.prose_fact_scan.extractor import bind_entity, extract_candidates
from cortex_store.prose_fact_scan.fp_controls import apply_fp_controls
from cortex_store.prose_fact_scan.gate import filter_active_eligible, is_gate_eligible
from cortex_store.prose_fact_scan.models import CandidateClause, FpCounters, ScanReport
from cortex_store.prose_fact_scan.output_writer import build_report_dict, write_scan_report
from cortex_store.prose_fact_scan.scanner import run_prose_fact_scan, scan_targets
from cortex_store.prose_fact_scan.schema import validate_report_json
from cortex_store.prose_fact_scan.target_resolver import expand_tier_a, resolve_scan_targets
from cortex_store.prose_fact_scan.verdict import apply_verdict
from cortex_store.prose_fact_scan.conftest import seed_tier_a_tree


def _asrt_row(
    *,
    asrt_id: int,
    entity_id: str,
    claim: str,
    confidence: str = "confirmed",
    review_status: str = "committed",
    predicate_form: str = "status(...)",
    events_json: str | None = None,
) -> dict[str, Any]:
    return {
        "id": asrt_id,
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "review_status": review_status,
        "predicate_form": predicate_form,
        "superseded_by": None,
        "valid_until": None,
        "events_json": events_json,
    }


def test_tierA_manifest_equals_glob_expansion_127(scan_base: Path) -> None:
    tier_a = expand_tier_a(scan_base)
    assert len(tier_a) == 127
    resolved = resolve_scan_targets(scan_base, unsafe_full_scan=True)
    manifest = resolved["manifest"]
    included = [m for m in manifest if m["reason"] == "included_tier_a"]
    assert len(included) == 127
    assert set(m["path"] for m in included) == set(tier_a.keys())
    assert len(TIER_A_GLOBS) == 8


def test_target_count_fail_closed_outside_80_200(tmp_path: Path) -> None:
    (tmp_path / "notes/system/handoffs").mkdir(parents=True)
    for i in range(5):
        (tmp_path / "notes/system/handoffs" / f"h{i}.md").write_text("# x\n")
    blocked = resolve_scan_targets(tmp_path)
    assert "error" in blocked
    allowed = resolve_scan_targets(tmp_path, unsafe_full_scan=True)
    assert "error" not in allowed


def test_excludes_archive_subtrees_zero_targets_opened(tmp_path: Path) -> None:
    seed_tier_a_tree(tmp_path)
    excluded_samples = [
        "notes/system/transcripts/t1.md",
        "notes/system/journal/j1.md",
        "notes/system/audit/a1.md",
        "notes/system/post-mortems/p1.md",
    ]
    for rel in excluded_samples:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# excluded\n")

    opened: list[str] = []

    def spy_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
        if hasattr(file, "as_posix"):
            opened.append(file.as_posix())
        elif isinstance(file, (str, Path)):
            opened.append(str(file))
        return builtins.open(file, *args, **kwargs)

    resolved = resolve_scan_targets(tmp_path, open_fn=spy_open, unsafe_full_scan=True)
    for rel in excluded_samples:
        assert not any(rel in o for o in opened)
        assert rel not in {t.path for t in resolved["targets"]}


TRIGGER_UBER = (
    "person:alex-uber currently has Uber driver account suspended by platform.\n"
)


def test_trigger_uber_suspended_flags_20479_unannot(tmp_path: Path) -> None:
    rel = "notes/system/shared/operational-context-test.md"
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TRIGGER_UBER, encoding="utf-8")

    def fetch(entity_id: str) -> list[dict[str, Any]]:
        if entity_id == "person:alex-uber":
            return [
                _asrt_row(
                    asrt_id=20479,
                    entity_id=entity_id,
                    claim="Uber driver account reinstated",
                    review_status="flagged",
                    predicate_form="status(uber,driver,reinstated)",
                )
            ]
        return []

    result = run_prose_fact_scan(
        paths=[rel],
        unsafe_full_scan=True,
        files_root=tmp_path,
        fetch_fn=fetch,
        search_fn=lambda _q: [],
        dry_run=True,
    )
    stale = [f for f in result["report"]["findings"] if f["verdict"] == "STALE"]
    assert len(stale) == 1
    assert stale[0]["assertion_id"] == 20479
    assert stale[0]["entity_id"] == "person:alex-uber"
    assert stale[0]["predicate_form"] == "status(...)"


EVENTSJSON_FIXTURE = (
    "person:rx-worker is currently deactivated from RxRelief pharmacy shifts.\n"
)


def test_second_eventsjson_fixture_flags_no_specialcase(tmp_path: Path) -> None:
    rel = "notes/system/context/rx.md"
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EVENTSJSON_FIXTURE, encoding="utf-8")

    def fetch(entity_id: str) -> list[dict[str, Any]]:
        if entity_id == "person:rx-worker":
            return [
                _asrt_row(
                    asrt_id=88001,
                    entity_id=entity_id,
                    claim="placement status unchanged",
                    predicate_form="status(rxrelief,engaged_in_pharmacy)",
                    events_json='["worker active on RxRelief shifts"]',
                )
            ]
        return []

    result = run_prose_fact_scan(
        paths=[rel],
        unsafe_full_scan=True,
        files_root=tmp_path,
        fetch_fn=fetch,
        search_fn=lambda _q: [],
        dry_run=True,
    )
    stale = [f for f in result["report"]["findings"] if f["verdict"] == "STALE"]
    assert len(stale) == 1
    assert stale[0]["assertion_id"] == 88001


@pytest.mark.parametrize(
    ("confidence", "review_status", "eligible"),
    [
        ("confirmed", "committed", True),
        ("confirmed", "flagged", True),
        ("believed", "committed", True),
        ("believed", "flagged", True),
        ("confirmed", "staged", False),
        ("confirmed", "dismissed", False),
        ("suspected", "committed", False),
        ("hypothesized", "flagged", False),
    ],
)
def test_gate_eligible_set_is_confirmed_believed_x_committed_flagged(
    confidence: str,
    review_status: str,
    eligible: bool,
) -> None:
    row = _asrt_row(
        asrt_id=1,
        entity_id="person:test",
        claim="active",
        confidence=confidence,
        review_status=review_status,
    )
    assert is_gate_eligible(row) is eligible
    filtered = filter_active_eligible([row])
    assert (len(filtered) == 1) is eligible


def test_search_miss_active_hit_still_stale(tmp_path: Path) -> None:
    rel = "notes/system/shared/operational-context-recall.md"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(
        "person:recall-test Uber account is currently suspended.\n"
    )

    def fetch(entity_id: str) -> list[dict[str, Any]]:
        return [
            _asrt_row(
                asrt_id=99001,
                entity_id=entity_id,
                claim="Uber reinstated",
                predicate_form="status(uber,driver,reinstated)",
            )
        ]

    result = run_prose_fact_scan(
        paths=[rel],
        unsafe_full_scan=True,
        files_root=tmp_path,
        fetch_fn=fetch,
        search_fn=lambda _q: [],
        dry_run=True,
    )
    assert any(f["verdict"] == "STALE" for f in result["report"]["findings"])


def test_active_miss_search_hit_not_stale(tmp_path: Path) -> None:
    rel = "notes/system/shared/operational-context-inverse.md"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(
        "person:inverse Uber account is currently suspended.\n"
    )

    def fetch(_entity_id: str) -> list[dict[str, Any]]:
        return []

    def search(_query: str) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": "person:inverse",
                "score": 0.95,
            }
        ]

    result = run_prose_fact_scan(
        paths=[rel],
        unsafe_full_scan=True,
        files_root=tmp_path,
        fetch_fn=fetch,
        search_fn=search,
        dry_run=True,
    )
    assert not any(f["verdict"] == "STALE" for f in result["report"]["findings"])


def test_citation_precorrection_wrongfenced_skipped() -> None:
    counters = FpCounters()
    text = "Uber is currently suspended.\n"
    for clause, reason in (
        ("Uber suspended → asrt 20479", "cited"),
        ("PRE-CORRECTION SNAPSHOT: Uber suspended", "precorrection"),
    ):
        skip, _ = apply_fp_controls(
            path="notes/system/shared/operational-context-x.md",
            clause=clause,
            full_text=text,
            bind_score=0.9,
            alignment_score=None,
            counters=counters,
        )
        assert skip
    fenced = "```\n# WRONG\nUber is currently suspended.\n```\n"
    skip, _ = apply_fp_controls(
        path="notes/system/shared/operational-context-x.md",
        clause="Uber is currently suspended.",
        full_text=fenced,
        bind_score=0.9,
        alignment_score=None,
        counters=counters,
    )
    assert skip
    assert counters.citation_skip >= 1
    assert counters.precorrection_skip >= 1
    assert counters.wrong_fenced_skip >= 1


def test_alignment_084_suppress_085_admit() -> None:
    candidate = CandidateClause(
        entity_id="person:a",
        fact_class="transport",
        predicate_form="status(...)",
        clause="Uber suspended",
        line_start=1,
        line_end=1,
    )
    row = _asrt_row(asrt_id=1, entity_id="person:a", claim="reinstated")
    counters_low = FpCounters()
    at_084 = apply_verdict(
        path="p.md",
        candidate=candidate,
        full_text=candidate.clause,
        verdict_hint="stale_candidate",
        row=row,
        alignment_score=0.84,
        counters=counters_low,
    )
    assert at_084 is not None
    assert at_084.verdict == "STALE"
    counters_high = FpCounters()
    at_085 = apply_verdict(
        path="p.md",
        candidate=candidate,
        full_text=candidate.clause,
        verdict_hint="stale_candidate",
        row=row,
        alignment_score=0.85,
        counters=counters_high,
    )
    assert at_085 is None or at_085.verdict != "STALE"
    assert counters_high.alignment_suppress >= 1


def test_bind_074_suppress_075_admit() -> None:
    entity_id, score, advisory = bind_entity(
        "Uber is currently suspended",
        principal="person:principal",
        search_fn=lambda _q: [{"entity_id": "person:bound", "score": 0.74}],
    )
    assert advisory
    entity_id2, score2, advisory2 = bind_entity(
        "Uber is currently suspended",
        principal="person:principal",
        search_fn=lambda _q: [{"entity_id": "person:bound", "score": 0.75}],
    )
    assert not advisory2
    assert entity_id2 == "person:bound"
    assert score2 == 0.75


def test_friction_one_per_file_entity_pair_with_asrt_id(tmp_path: Path) -> None:
    rel = "notes/system/shared/operational-context-multi.md"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(
        "person:multi Uber is currently suspended.\n"
        "person:multi driver account remains suspended.\n"
    )

    def fetch(entity_id: str) -> list[dict[str, Any]]:
        return [
            _asrt_row(
                asrt_id=20479,
                entity_id=entity_id,
                claim="Uber reinstated",
            )
        ]

    emitted: list[dict[str, Any]] = []

    def friction_fn(**kwargs: Any) -> dict[str, Any]:
        emitted.append(kwargs)
        return {"item": {"id": 50000 + len(emitted)}}

    result = run_prose_fact_scan(
        paths=[rel],
        unsafe_full_scan=True,
        files_root=tmp_path,
        fetch_fn=fetch,
        search_fn=lambda _q: [],
        friction_fn=friction_fn,
        dry_run=False,
    )
    assert len(emitted) == 1
    assert "20479" in emitted[0]["note"]
    assert len(result["friction_ids"]) == 1


def test_report_json_schema_and_no_prose_write(tmp_path: Path) -> None:
    rel = "notes/system/shared/operational-context-report.md"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text("person:r Uber is currently suspended.\n")

    md_writes: list[str] = []

    real_open = builtins.open

    def tracking_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        path_str = str(file)
        if "w" in mode and path_str.endswith(".md") and REPORT_DIR not in path_str:
            md_writes.append(path_str)
        return real_open(file, mode, *args, **kwargs)

    with patch("builtins.open", side_effect=tracking_open):
        result = run_prose_fact_scan(
            paths=[rel],
            unsafe_full_scan=True,
            files_root=tmp_path,
            fetch_fn=lambda e: [
                _asrt_row(asrt_id=1, entity_id=e, claim="Uber reinstated")
            ],
            search_fn=lambda _q: [],
            dry_run=False,
            open_fn=tracking_open,
        )
    assert not md_writes
    assert validate_report_json(result["report"]) == []
    assert (tmp_path / result["report_path"]).is_file()
