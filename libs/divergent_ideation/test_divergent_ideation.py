"""Unit tests for divergent-ideation-v1 metric and assignment functions (V-3)."""

from __future__ import annotations

import multiprocessing as mp

import pytest

from divergent_ideation.audit_assign_v1 import assign_auditors
from divergent_ideation.collate_v1 import (
    build_run_manifest,
    compute_convergence_rate,
    map_success_fraction,
)
from divergent_ideation.leak_check_v1 import check_leak, finalize_leak_reports
from divergent_ideation.operator_split_v1 import split_operators


def _pool(size: int) -> list[dict[str, str]]:
    families = ["f0", "f1", "f2", "f3", "f4"]
    return [
        {"model": f"m{i}", "family": families[i % len(families)]} for i in range(size)
    ]


def _brief(bid: str, author: str, family: str) -> dict:
    return {"brief_id": bid, "author_model": author, "author_family": family}


def test_operator_split_disjoint_union_and_dispersion_bounds() -> None:
    operators = [
        {"doc_id": f"d{i}", "chunk_id": "c0", "operator_statement": "x"} for i in range(6)
    ]
    pool = _pool(3)
    result = split_operators(operators, pool, run_seed=42)
    subsets = result["operator_subsets"]
    assert len(subsets) == 3
    flat = [x for s in subsets for x in s]
    assert len(flat) == len(set(flat))
    assert len(flat) == 6
    assert 0.0 <= result["operator_dispersion"] <= 1.0
    assert result["pool_models"] == ["m0", "m1", "m2"]


def test_operator_split_deterministic_across_processes() -> None:
    operators = [{"doc_id": "d1", "chunk_id": "a", "operator_statement": "a"}]

    def worker(q: mp.Queue) -> None:
        q.put(split_operators(operators, _pool(2), 99))

    q1: mp.Queue = mp.Queue()
    q2: mp.Queue = mp.Queue()
    p1 = mp.Process(target=worker, args=(q1,))
    p2 = mp.Process(target=worker, args=(q2,))
    p1.start()
    p2.start()
    p1.join()
    p2.join()
    assert q1.get() == q2.get()


def test_leak_check_variants_and_finalize() -> None:
    abstract = {"structural_goal": "STORAGE spillover pattern", "constraints": []}
    report = check_leak(abstract, ["storage"])
    assert report["passed"] is False
    report2 = check_leak({"structural_goal": "neutral goal", "constraints": []}, ["storage"])
    assert report2["passed"] is True
    final, accepted = finalize_leak_reports(
        [{"passed": True, "hits": [], "attempts": 1}, {"_skipped": True}],
        [{"structural_goal": "ok", "constraints": []}, None],
    )
    assert final["passed"] is True
    assert accepted["structural_goal"] == "ok"


@pytest.mark.parametrize("size", [3, 4, 5])
def test_audit_assign_cross_family_and_distinct(size: int) -> None:
    pool = _pool(size)
    briefs = [_brief("b1", "m0", "f0"), _brief("b2", "m1", "f1")]
    out = assign_auditors(briefs, pool, run_seed=7, min_auditors=2)
    items = out["audit_assignments"]
    assert not out["assignment_error"]
    by_brief: dict[str, set[str]] = {}
    for item in items:
        assert item["auditor_model"] != item["author_model"]
        by_brief.setdefault(item["brief_id"], set()).add(item["auditor_model"])
    for auditors in by_brief.values():
        assert len(auditors) == 2


def test_audit_assign_pool_too_small_and_duplicate_pool() -> None:
    small = assign_auditors([_brief("b", "m0", "f0")], _pool(2), 1, min_auditors=2)
    assert small["audit_assignments"] == []
    assert "pool_too_small" in small["assignment_error"]
    dup_pool = [{"model": "a", "family": "f"}, {"model": "a", "family": "g"}, {"model": "b", "family": "h"}]
    dup = assign_auditors([_brief("b", "a", "f")], dup_pool, 1)
    assert dup["audit_assignments"] == []
    assert "pool_invalid" in dup["assignment_error"]


def test_audit_assign_seed_sensitivity() -> None:
    briefs = [_brief("b1", "m0", "f0")]
    pool = _pool(4)
    a = assign_auditors(briefs, pool, run_seed=1)
    b = assign_auditors(briefs, pool, run_seed=2)
    assert a["audit_assignments"] != b["audit_assignments"]


def test_collate_convergence_and_parse_failures() -> None:
    briefs = [
        _brief("b1", "m0", "f0"),
        _brief("b2", "m1", "f1"),
    ]
    audits = [
        {
            "brief_id": "b1",
            "auditor_family": "f1",
            "mechanism_class": "Alpha",
            "re_instantiation_faithful": True,
            "template_match": "novel",
        },
        {
            "brief_id": "b2",
            "auditor_family": "f0",
            "mechanism_class": "alpha",
            "re_instantiation_faithful": True,
            "template_match": "variant",
        },
    ]
    rate, falsified, clusters = compute_convergence_rate(briefs, audits)
    assert rate == 1.0
    assert falsified is True
    assert clusters
    manifest = build_run_manifest(
        {
            "run_id": "r1",
            "problem_id": "p1",
            "run_seed": 1,
            "pool_manifest": _pool(3),
            "leak_report": {"passed": False, "hits": [{"term": "x"}], "attempts": 1},
            "briefs": [{"brief_id": "b", "author_model": "m0"}],
            "audits": [],
            "skipped_steps": {"map_ideate": True},
            "assignment_error": None,
            "map_failure": False,
            "operator_dispersion": 0.5,
            "operator_subsets": [],
            "abstract_briefs": [],
            "audit_assignments": [],
        }
    )
    assert manifest["halted_at"] == "map_ideate"
    assert manifest["halt_reason"] == "leak"
    bad = build_run_manifest(
        {
            "run_id": "r1",
            "problem_id": "p1",
            "run_seed": 1,
            "pool_manifest": _pool(3),
            "leak_report": {"passed": True, "hits": [], "attempts": 1},
            "briefs": [{"brief_id": "b", "author_model": "m0"}],
            "audits": [],
            "skipped_steps": {},
            "assignment_error": None,
            "map_failure": False,
            "operator_dispersion": 0.5,
            "operator_subsets": [],
            "abstract_briefs": [],
            "audit_assignments": [],
        }
    )
    assert bad["parse_failures"]


def test_map_success_fraction() -> None:
    assert map_success_fraction(failed=3, total=10) is False
    assert map_success_fraction(failed=6, total=10) is True
