"""Unit tests for divergent-ideation-v1 metric and assignment functions (V-3)."""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from systems.pipeline.core.execution.map_reduce.collection import MapOutputCollection
from systems.pipeline.core.handlers.step_output import StepOutput
from systems.pipeline.core.schemas import ReadsFrom, StepConfig

from divergent_ideation.audit_assign_v1 import assign_auditors
from divergent_ideation.collate_v1 import (
    build_run_manifest,
    compute_convergence_rate,
    map_success_fraction,
)
from divergent_ideation.leak_check_v1 import (
    apply_problem_defaults,
    build_rag_query,
    check_leak,
    finalize_leak_reports,
)
from divergent_ideation.map_enrich_v1 import (
    abstract_briefs_from_map_ideate,
    briefs_from_re_instantiate,
    model_id_mismatches,
)
from divergent_ideation.operator_split_v1 import split_operators

SERVED_POOL_MODELS = (
    "qwen3-5-9b-q8-0-131072",
    "gemma-4-26b-a4b-it-q4-k-m-65536",
    "openai/gpt-5.4",
)


def _pool(size: int) -> list[dict[str, str]]:
    families = ["f0", "f1", "f2", "f3", "f4"]
    return [
        {"model": f"m{i}", "family": families[i % len(families)]} for i in range(size)
    ]


def test_divergent_ideation_v1_yaml_default_pool_and_models() -> None:
    """F2-A: YAML defaults and models.yaml name only the three served pool ids."""
    repo_root = Path(__file__).resolve().parents[2]
    spec_dict = yaml.safe_load(
        (repo_root / "pipelines/divergent_ideation/v1/divergent-ideation-v1.yaml").read_text()
    )
    pool = spec_dict["options"]["pool_manifest"]
    assert [entry["model"] for entry in pool] == list(SERVED_POOL_MODELS)
    models_yaml = yaml.safe_load(
        (repo_root / "pipelines/divergent_ideation/models.yaml").read_text()
    )
    for alias in ("decontextualize", "map_static"):
        assert models_yaml["models"][alias]["model"] == SERVED_POOL_MODELS[0]


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


def test_leak_check_stemmed_variant() -> None:
    abstract = {"structural_goal": "computational spillover pattern", "constraints": []}
    report = check_leak(abstract, ["computing"])
    assert report["passed"] is False
    assert report["hits"][0]["variant_matched"] == "comput"


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


def test_leak_finalize_attempts_count() -> None:
    reports = [
        {"passed": False, "hits": [{"term": "x"}], "attempts": 1},
        {"passed": True, "hits": [], "attempts": 1},
    ]
    abstracts = [{"structural_goal": "a", "constraints": []}, {"structural_goal": "b", "constraints": []}]
    final, _ = finalize_leak_reports(reports, abstracts)
    assert final["attempts"] == 2


def test_build_rag_query_joins_constraints() -> None:
    abstract = {
        "structural_goal": "goal",
        "constraints": [{"statement": "c1"}, {"statement": "c2"}],
    }
    assert build_rag_query(abstract) == "goal c1 c2"


def test_build_rag_query_includes_string_constraints() -> None:
    abstract = {
        "structural_goal": "goal",
        "constraints": ["plain constraint"],
    }
    assert build_rag_query(abstract) == "goal plain constraint"


def test_leak_check_string_actor_items() -> None:
    abstract = {
        "structural_goal": "neutral",
        "actors": ["storage operator"],
        "constraints": [],
    }
    report = check_leak(abstract, ["storage"])
    assert report["passed"] is False


def test_apply_problem_defaults_run_seed() -> None:
    assert apply_problem_defaults({"problem_id": "p"}, default_run_seed=42)["run_seed"] == 42
    assert apply_problem_defaults({"run_seed": 7}, default_run_seed=42)["run_seed"] == 7


def test_divergent_ideation_v1_yaml_run_seed_default() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    spec_dict = yaml.safe_load(
        (repo_root / "pipelines/divergent_ideation/v1/divergent-ideation-v1.yaml").read_text()
    )
    assert spec_dict["options"]["run_seed"]["default"] == 0
    step_names = [s["name"] for s in spec_dict["steps"]]
    assert "problem_defaults" in step_names


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


def test_audit_assign_cross_family_before_same_family() -> None:
    pool = [
        {"model": "m0", "family": "f0"},
        {"model": "m1", "family": "f0"},
        {"model": "m2", "family": "f1"},
        {"model": "m3", "family": "f1"},
    ]
    briefs = [_brief("b1", "m0", "f0")]
    out = assign_auditors(briefs, pool, run_seed=1, min_auditors=2)
    auditors = [x["auditor_model"] for x in out["audit_assignments"]]
    assert auditors[0] in {"m2", "m3"}


def test_audit_assign_pool_too_small_and_duplicate_pool() -> None:
    small = assign_auditors([_brief("b", "m0", "f0")], _pool(2), 1, min_auditors=2)
    assert small["audit_assignments"] == []
    assert "pool_too_small" in small["assignment_error"]
    dup_pool = [{"model": "a", "family": "f"}, {"model": "a", "family": "g"}, {"model": "b", "family": "h"}]
    dup = assign_auditors([_brief("b", "a", "f")], dup_pool, 1)
    assert dup["audit_assignments"] == []
    assert "pool_invalid" in dup["assignment_error"]


def test_audit_assign_author_absent_from_pool() -> None:
    out = assign_auditors([_brief("b", "missing", "f0")], _pool(4), 1)
    assert out["audit_assignments"] == []
    assert "pool_invalid" in out["assignment_error"]


def test_audit_assign_alias_routing_key_mismatch() -> None:
    pool = [
        {"model": "qwen-alias", "family": "f0"},
        {"model": "m1", "family": "f1"},
        {"model": "m2", "family": "f2"},
    ]
    briefs = [_brief("b1", "qwen3-5-resolved-key", "f0")]
    out = assign_auditors(briefs, pool, run_seed=3)
    assert out["audit_assignments"] == []
    assert "pool_invalid" in out["assignment_error"]


def test_audit_assign_seed_sensitivity() -> None:
    briefs = [_brief("b1", "m0", "f0")]
    pool = _pool(4)
    a = assign_auditors(briefs, pool, run_seed=1)
    b = assign_auditors(briefs, pool, run_seed=2)
    assert a["audit_assignments"] != b["audit_assignments"]


def test_audit_assign_deterministic_across_processes() -> None:
    briefs = [_brief("b1", "m0", "f0")]
    pool = _pool(4)

    def worker(q: mp.Queue) -> None:
        q.put(assign_auditors(briefs, pool, run_seed=11))

    q1: mp.Queue = mp.Queue()
    q2: mp.Queue = mp.Queue()
    p1 = mp.Process(target=worker, args=(q1,))
    p2 = mp.Process(target=worker, args=(q2,))
    p1.start()
    p2.start()
    p1.join()
    p2.join()
    assert q1.get() == q2.get()


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
    assert manifest["leak_report"]["hits"]
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


def test_convergence_tie_assigns_each_tied_label() -> None:
    briefs = [_brief("b1", "m0", "f0")]
    audits = [
        {
            "brief_id": "b1",
            "auditor_family": "f1",
            "mechanism_class": "Alpha",
            "re_instantiation_faithful": True,
        },
        {
            "brief_id": "b1",
            "auditor_family": "f2",
            "mechanism_class": "Beta",
            "re_instantiation_faithful": True,
        },
    ]
    _, _, clusters = compute_convergence_rate(briefs, audits)
    labels = {c["label"] for c in clusters if "b1" in c["brief_ids"]}
    assert labels == {"alpha", "beta"}


def test_convergence_unfaithful_excluded_from_rate() -> None:
    briefs = [_brief("b1", "m0", "f0"), _brief("b2", "m1", "f1")]
    audits = [
        {
            "brief_id": "b1",
            "auditor_family": "f1",
            "mechanism_class": "X",
            "re_instantiation_faithful": False,
        },
        {
            "brief_id": "b2",
            "auditor_family": "f0",
            "mechanism_class": "Y",
            "re_instantiation_faithful": True,
        },
    ]
    rate, _, _ = compute_convergence_rate(briefs, audits)
    assert rate == 0.0


def test_convergence_zero_when_no_cluster_spans_two_families() -> None:
    briefs = [_brief("b1", "m0", "f0"), _brief("b2", "m0", "f0")]
    audits = [
        {
            "brief_id": "b1",
            "auditor_family": "f1",
            "mechanism_class": "Same",
            "re_instantiation_faithful": True,
        },
        {
            "brief_id": "b2",
            "auditor_family": "f1",
            "mechanism_class": "Same",
            "re_instantiation_faithful": True,
        },
    ]
    rate, falsified, _ = compute_convergence_rate(briefs, audits)
    assert rate == 0.0
    assert falsified is False


def test_collate_skipped_step_sets_halt() -> None:
    manifest = build_run_manifest(
        {
            "run_id": "r1",
            "problem_id": "p1",
            "run_seed": 1,
            "pool_manifest": _pool(3),
            "leak_report": {"passed": True, "hits": [], "attempts": 1},
            "briefs": [],
            "audits": [],
            "skipped_steps": {"retrieve_operators": True},
            "assignment_error": None,
            "map_failure": False,
            "operator_dispersion": 0.0,
            "operator_subsets": [],
            "abstract_briefs": [],
            "audit_assignments": [],
        }
    )
    assert manifest["halted_at"] == "retrieve_operators"
    assert manifest["halt_reason"] == "condition_error"


def test_collate_list_order_binding() -> None:
    manifest = build_run_manifest(
        {
            "run_id": "r1",
            "problem_id": "p1",
            "run_seed": 1,
            "pool_manifest": _pool(3),
            "leak_report": {"passed": True, "hits": [], "attempts": 1},
            "briefs": [
                {"brief_id": "b2", "author_model": "m0", "domain_statement": "x", "falsifier": "f", "cost_to_test": "c", "time_to_signal": "t"},
                {"brief_id": "b1", "author_model": "m0", "domain_statement": "y", "falsifier": "f", "cost_to_test": "c", "time_to_signal": "t"},
            ],
            "audits": [
                {"brief_id": "b2", "auditor_model": "a2", "mechanism_class": "m", "template_match": "novel", "re_instantiation_faithful": True},
                {"brief_id": "b1", "auditor_model": "a1", "mechanism_class": "m", "template_match": "novel", "re_instantiation_faithful": True},
            ],
            "audit_assignments": [
                {"brief_id": "b2", "auditor_model": "a2"},
                {"brief_id": "b1", "auditor_model": "a1"},
            ],
            "skipped_steps": {},
            "assignment_error": None,
            "map_failure": False,
            "operator_dispersion": 0.0,
            "operator_subsets": [],
            "abstract_briefs": [],
        }
    )
    assert [b["brief_id"] for b in manifest["briefs"]] == ["b1", "b2"]
    assert [a["brief_id"] for a in manifest["audits"]] == ["b1", "b2"]


def test_map_success_fraction() -> None:
    assert map_success_fraction(failed=3, total=10) is False
    assert map_success_fraction(failed=6, total=10) is True


def test_unfaithful_briefs_remain_in_manifest() -> None:
    manifest = build_run_manifest(
        {
            "run_id": "r1",
            "problem_id": "p1",
            "run_seed": 1,
            "pool_manifest": _pool(3),
            "leak_report": {"passed": True, "hits": [], "attempts": 1},
            "briefs": [
                {
                    "brief_id": "b1",
                    "author_model": "m0",
                    "author_family": "f0",
                    "domain_statement": "d",
                    "falsifier": "f",
                    "cost_to_test": "c",
                    "time_to_signal": "t",
                }
            ],
            "audits": [
                {
                    "brief_id": "b1",
                    "auditor_family": "f1",
                    "mechanism_class": "Z",
                    "re_instantiation_faithful": False,
                    "template_match": "novel",
                }
            ],
            "skipped_steps": {},
            "assignment_error": None,
            "map_failure": False,
            "operator_dispersion": 0.0,
            "operator_subsets": [],
            "abstract_briefs": [],
            "audit_assignments": [],
        }
    )
    assert len(manifest["briefs"]) == 1
    assert manifest["convergence_rate"] == 0.0


def _step_output(model_id: str, json_payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(model_id=model_id, json=json_payload)


def test_leak_check_final_stepconfig_reads_from_uses_step_field() -> None:
    step_dict = {
        "name": "leak_check_final",
        "type": "leak_check_v1",
        "reads_from": [
            {"step": "leak_check_0", "fields": ["json"]},
            {"step": "leak_check_1", "fields": ["json"]},
            {"step": "leak_check_2", "fields": ["json"]},
        ],
    }
    step = StepConfig.model_validate(step_dict)
    names: list[str] = []
    for entry in step.reads_from:
        assert isinstance(entry, ReadsFrom)
        names.append(entry.step)
    assert names == ["leak_check_0", "leak_check_1", "leak_check_2"]


@pytest.mark.asyncio
async def test_leak_check_finalize_execute_reads_from_without_get() -> None:
    from pipelines.divergent_ideation.v1.handlers.leak_check import LeakCheckV1Handler

    step = StepConfig.model_validate(
        {
            "name": "leak_check_final",
            "type": "leak_check_v1",
            "reads_from": [{"step": "leak_check_0", "fields": ["json"]}],
        }
    )

    class Ctx:
        def get_output(self, name: str) -> SimpleNamespace:
            return _step_output(
                "",
                {
                    "leak_report": {"passed": True, "hits": [], "attempts": 1},
                    "accepted_abstract_problem": {
                        "structural_goal": "goal",
                        "constraints": [],
                    },
                },
            )

    out = await LeakCheckV1Handler()._execute_finalize(step, Ctx())  # type: ignore[arg-type]
    assert out.json is not None
    assert out.json["passed"] is True


def test_briefs_from_re_instantiate_resolves_engine_brief_id() -> None:
    run_id = "run18011"
    abstract_briefs = [
        {
            "brief_id": f"{run_id}-0-0",
            "author_model": "m0",
            "author_family": "f0",
        },
        {
            "brief_id": f"{run_id}-2-1",
            "author_model": "m2",
            "author_family": "f2",
        },
    ]
    outputs = [
        _step_output(
            "m0",
            {
                "briefs": [
                    {
                        "abstract_index": 0,
                        "domain_statement": "d0",
                        "falsifier": "f",
                        "cost_to_test": "c",
                        "time_to_signal": "t",
                    }
                ]
            },
        ),
        _step_output(
            "m2",
            {
                "briefs": [
                    {
                        "abstract_index": 1,
                        "domain_statement": "d1",
                        "falsifier": "f",
                        "cost_to_test": "c",
                        "time_to_signal": "t",
                    }
                ]
            },
        ),
    ]
    briefs, failures = briefs_from_re_instantiate(
        outputs,
        abstract_briefs,
        _pool(3),
        run_id=run_id,
        ideate_output_positions=[0, 2],
    )
    assert failures == []
    assert [b["brief_id"] for b in briefs] == [f"{run_id}-0-0", f"{run_id}-2-1"]


def test_briefs_from_re_instantiate_unresolved_ref_is_parse_failure() -> None:
    run_id = "run18011"
    outputs = [
        _step_output(
            "m0",
            {
                "briefs": [
                    {
                        "abstract_ref": "free-text-id",
                        "domain_statement": "d",
                        "falsifier": "f",
                        "cost_to_test": "c",
                        "time_to_signal": "t",
                    }
                ]
            },
        )
    ]
    briefs, failures = briefs_from_re_instantiate(
        outputs,
        [],
        _pool(3),
        run_id=run_id,
        ideate_output_positions=[0],
    )
    assert briefs == []
    assert failures and "abstract_index" in failures[0]["error"]


def test_re_instantiate_planned_models_match_ideate_successes_only() -> None:
    """After one failed map_ideate slot, zip re outputs with all_outputs model ids."""
    success_outputs = [_step_output("m0", {}), _step_output("m2", {})]
    planned = [str(o.model_id) for o in success_outputs]
    re_outputs = [_step_output("m0", {}), _step_output("m2", {})]
    assert model_id_mismatches(re_outputs, planned, "re_instantiate") == []
    aligned_planned = ["m0", "m1", "m2"]
    mismatches = model_id_mismatches(re_outputs, aligned_planned, "re_instantiate")
    assert mismatches and mismatches[0]["model"] == "m2"
    assert "m1" in mismatches[0]["error"]


def _domain_brief(abstract_index: int) -> dict[str, Any]:
    return {
        "abstract_index": abstract_index,
        "domain_statement": "d",
        "falsifier": "f",
        "cost_to_test": "c",
        "time_to_signal": "t",
    }


def _ideate_step_output(model_id: str) -> StepOutput:
    return StepOutput(
        raw="{}",
        model_id=model_id,
        json={"briefs": [{"structural_goal": "goal", "constraints": []}]},
    )


def test_re_instantiate_model_id_mismatches_aligned_after_leading_failure() -> None:
    """Element 0 failed; aligned zip with ideate plan yields no mismatch rows."""
    ideate_successes = [_ideate_step_output("m0"), _ideate_step_output("m1"), _ideate_step_output("m2")]
    planned = [str(o.model_id or "") for o in ideate_successes]
    re_inst = MapOutputCollection(
        [
            StepOutput(raw="{}", model_id="m1", json={"briefs": [_domain_brief(0)]}),
            StepOutput(raw="{}", model_id="m2", json={"briefs": [_domain_brief(0)]}),
        ],
        output_positions=[1, 2],
        total_count=3,
        failed_iteration_hints=[{"index": 0, "error_message": "failed"}],
    )
    assert model_id_mismatches(re_inst.outputs_aligned(), planned, "re_instantiate") == []


def test_re_instantiate_model_id_mismatches_aligned_single_wrong_model() -> None:
    """Element 0 failed; one later slot served the wrong model at its aligned index."""
    ideate_successes = [_ideate_step_output("m0"), _ideate_step_output("m1"), _ideate_step_output("m2")]
    planned = [str(o.model_id or "") for o in ideate_successes]
    re_inst = MapOutputCollection(
        [
            StepOutput(raw="{}", model_id="m1", json={"briefs": [_domain_brief(0)]}),
            StepOutput(raw="{}", model_id="m-wrong", json={"briefs": [_domain_brief(0)]}),
        ],
        output_positions=[1, 2],
        total_count=3,
        failed_iteration_hints=[{"index": 0, "error_message": "failed"}],
    )
    mismatches = model_id_mismatches(re_inst.outputs_aligned(), planned, "re_instantiate")
    assert len(mismatches) == 1
    assert mismatches[0]["iteration_index"] == 2
    assert mismatches[0]["model"] == "m-wrong"
    assert "m2" in mismatches[0]["error"]
    assert "m-wrong" in mismatches[0]["error"]


def test_re_instantiate_aligned_outputs_preserve_pool_index_after_leading_failure() -> None:
    """Element 0 failed re_instantiate; later briefs must keep surviving pool indices."""
    exec_id = "EXEC-18011"
    pool = _pool(3)
    pool_models = ["m0", "m1", "m2"]
    ideate_successes = [_ideate_step_output("m0"), _ideate_step_output("m1"), _ideate_step_output("m2")]
    map_ideate = MapOutputCollection(
        ideate_successes,
        output_positions=[0, 1, 2],
        total_count=3,
    )
    re_successes = [
        StepOutput(raw="{}", model_id="m1", json={"briefs": [_domain_brief(0)]}),
        StepOutput(raw="{}", model_id="m2", json={"briefs": [_domain_brief(0)]}),
    ]
    re_inst = MapOutputCollection(
        re_successes,
        output_positions=[1, 2],
        total_count=3,
        failed_iteration_hints=[{"index": 0, "error_message": "failed"}],
    )
    ideate_positions = list(map_ideate._output_positions or ())
    abstract_briefs = abstract_briefs_from_map_ideate(
        map_ideate.outputs_aligned(),
        pool_models,
        pool,
        exec_id,
        briefs_per_model=1,
    )
    briefs, failures = briefs_from_re_instantiate(
        re_inst.outputs_aligned(),
        abstract_briefs,
        pool,
        run_id=exec_id,
        ideate_output_positions=ideate_positions,
    )
    assert failures == []
    assert [b["brief_id"] for b in briefs] == [f"{exec_id}-1-0", f"{exec_id}-2-0"]
    assert [b["author_model"] for b in briefs] == ["m1", "m2"]

    shifted, _ = briefs_from_re_instantiate(
        re_inst.all_outputs(),
        abstract_briefs,
        pool,
        run_id=exec_id,
        ideate_output_positions=ideate_positions,
    )
    assert [b["brief_id"] for b in shifted] == [f"{exec_id}-0-0", f"{exec_id}-1-0"]

    assign_out = assign_auditors(briefs, pool, run_seed=18011, min_auditors=2)
    assert not assign_out["assignment_error"]
    for row in assign_out["audit_assignments"]:
        assert row["auditor_model"] != row["author_model"]


@pytest.mark.asyncio
async def test_collate_handler_manifest_run_id_from_execution_id() -> None:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import PipelineOptions, PipelineSpec

    from pipelines.divergent_ideation.v1.handlers.collate import CollateV1Handler

    exec_id = "EXEC-COLLATE-RUN-ID"
    step = StepConfig.model_validate({"name": "collate", "type": "collate_v1"})
    pipeline = PipelineSpec(
        id="divergent-ideation-v1",
        version="1",
        type="test",
        category="test",
        steps=[],
        output="collate",
        options=PipelineOptions(),
        source_search_path="pipelines",
    )
    ctx = PipelineContext(
        pipeline=pipeline,
        source_text="",
        http_request=None,  # type: ignore[arg-type]
        execution_id=exec_id,
    )
    ctx.outputs["operator_split"] = StepOutput(raw="{}", json={"pool_models": []})

    out = await CollateV1Handler().execute(step, ctx)
    assert out.json is not None
    assert out.json["run_id"] == exec_id


@pytest.mark.asyncio
async def test_collate_handler_leak_halt_skipped_upstream_steps() -> None:
    """A-2.6: collate resolves whole-step JSON bindings when gated steps are skipped."""
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import PipelineOptions, PipelineSpec

    from pipelines.divergent_ideation.v1.handlers.collate import CollateV1Handler

    repo_root = Path(__file__).resolve().parents[2]
    spec_dict = yaml.safe_load(
        (repo_root / "pipelines/divergent_ideation/v1/divergent-ideation-v1.yaml").read_text()
    )
    collate_dict = next(s for s in spec_dict["steps"] if s["name"] == "collate")
    step = StepConfig.model_validate(collate_dict)

    skipped = StepOutput(raw="", json={"_skipped": True})
    leak_report = {"passed": False, "hits": [{"term": "secret"}], "attempts": 3}
    pipeline = PipelineSpec(
        id="divergent-ideation-v1",
        version="1",
        type="test",
        category="test",
        steps=[],
        output="collate",
        options=PipelineOptions.model_validate(spec_dict.get("options") or {}),
        source_search_path="pipelines",
    )
    ctx = PipelineContext(
        pipeline=pipeline,
        source_text="{}",
        http_request=None,  # type: ignore[arg-type]
        execution_id="EXEC-LEAK-HALT",
    )
    problem = {"problem_id": "p1", "run_seed": 1, "benchmark_tag": None}
    ctx.outputs["problem_defaults"] = StepOutput(raw="{}", json=problem)
    ctx.outputs["leak_check_final"] = StepOutput(
        raw="{}",
        json={
            "passed": False,
            "leak_report": leak_report,
            "accepted_abstract_problem": {},
            "rag_query": "",
        },
    )
    for name in (
        "retrieve_operators",
        "operator_split",
        "map_ideate",
        "re_instantiate",
        "audit_assign",
        "map_audit",
    ):
        ctx.outputs[name] = skipped

    out = await CollateV1Handler().execute(step, ctx)

    assert out.json is not None
    assert out.json["halted_at"] == "retrieve_operators"
    assert out.json["halt_reason"] == "leak"
    assert out.json["leak_report"]["hits"]


def test_audit_assign_and_collate_brief_ids_share_execution_namespace() -> None:
    """Metadata run_id META must not replace context.execution_id EXEC in brief_id."""
    exec_id = "EXEC"
    meta_run_id = "META"
    pool = _pool(3)
    pool_models = ["m0", "m1", "m2"]
    map_ideate = MapOutputCollection(
        [_ideate_step_output("m0")],
        output_positions=[0],
        total_count=1,
    )
    re_inst = MapOutputCollection(
        [StepOutput(raw="{}", model_id="m0", json={"briefs": [_domain_brief(0)]})],
        output_positions=[0],
        total_count=1,
    )
    ideate_positions = list(map_ideate._output_positions or ())
    abstract_briefs = abstract_briefs_from_map_ideate(
        map_ideate.outputs_aligned(),
        pool_models,
        pool,
        exec_id,
        briefs_per_model=1,
    )
    audit_briefs, _ = briefs_from_re_instantiate(
        re_inst.outputs_aligned(),
        abstract_briefs,
        pool,
        run_id=exec_id,
        ideate_output_positions=ideate_positions,
    )
    collate_briefs, _ = briefs_from_re_instantiate(
        re_inst.outputs_aligned(),
        abstract_briefs,
        pool,
        run_id=exec_id,
        ideate_output_positions=ideate_positions,
    )
    assert {b["brief_id"] for b in audit_briefs} == {b["brief_id"] for b in collate_briefs}
    assert audit_briefs[0]["brief_id"] == f"{exec_id}-0-0"
    meta_briefs, meta_failures = briefs_from_re_instantiate(
        re_inst.outputs_aligned(),
        abstract_briefs,
        pool,
        run_id=meta_run_id,
        ideate_output_positions=ideate_positions,
    )
    assert meta_briefs == []
    assert meta_failures and "META-0-0" in meta_failures[0].get("error", "")
    assert audit_briefs[0]["brief_id"] == f"{exec_id}-0-0"
    assert meta_run_id != exec_id


def test_assign_auditors_nine_engine_brief_ids() -> None:
    run_id = "run18011"
    pool = _pool(3)
    briefs = [
        _brief(f"{run_id}-{i // 3}-{i % 3}", f"m{i // 3}", f"f{i // 3}")
        for i in range(9)
    ]
    out = assign_auditors(briefs, pool, run_seed=18011, min_auditors=2)
    assert not out["assignment_error"]
    by_brief: dict[str, set[str]] = {}
    for item in out["audit_assignments"]:
        by_brief.setdefault(item["brief_id"], set()).add(item["auditor_model"])
    assert len(by_brief) == 9
    for auditors in by_brief.values():
        assert len(auditors) == 2
