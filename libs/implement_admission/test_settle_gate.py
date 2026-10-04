"""Settle-gate breaks from the 14901 bind."""

from implement_admission.settle_gate import (
    gateway_id_sets_equal,
    judge_event_window,
    judge_settle,
)


def test_equal_length_different_members_is_not_pass():
    assert not gateway_id_sets_equal(["gw-a", "gw-b"], ["gw-a", "gw-c"])
    verdict = judge_settle(
        snapshot_gateway_ids=["gw-a", "gw-b"],
        snapshot_pipeline_ids=["p"],
        post_gateway_ids=["gw-a", "gw-c"],
        post_pipeline_ids=["p"],
        membership_seq=3,
        latest_catalog_seq=None,
        timed_out=False,
    )
    assert verdict != "pass"
    assert verdict == "indeterminate"


def test_membership_before_catalog_reload_is_indeterminate():
    """Membership that precedes catalog.changed must not count as post-reload ready."""
    verdict = judge_event_window(
        [
            {"signal": "federation.catalog.changed", "seq": 10, "payload": {}},
            {
                "signal": "federation.gateway.membership",
                "seq": 9,
                "payload": {"gateway_ids": ["gw-a"], "pipeline_ids": ["p"]},
            },
        ],
        snapshot_gateway_ids=["gw-a"],
        snapshot_pipeline_ids=["p"],
        timed_out=False,
    )
    assert verdict == "indeterminate"


def test_cap_expiry_while_catalog_events_arrive_is_indeterminate():
    verdict = judge_event_window(
        [
            {"signal": "federation.catalog.changed", "seq": 4, "payload": {}},
            {
                "signal": "federation.gateway.membership",
                "seq": 5,
                "payload": {"gateway_ids": ["gw-a"], "pipeline_ids": ["p"]},
            },
        ],
        snapshot_gateway_ids=["gw-a"],
        snapshot_pipeline_ids=["p"],
        timed_out=True,
    )
    assert verdict == "indeterminate"


def test_missing_snapshot_is_indeterminate():
    verdict = judge_settle(
        snapshot_gateway_ids=None,
        snapshot_pipeline_ids=None,
        post_gateway_ids=["gw-a"],
        post_pipeline_ids=["p"],
        membership_seq=1,
        latest_catalog_seq=None,
        timed_out=False,
    )
    assert verdict == "indeterminate"


def test_gateway_state_changed_url_is_not_a_membership_key():
    verdict = judge_event_window(
        [
            {
                "signal": "gateway.state.changed",
                "seq": 2,
                "payload": {"url": "http://gw-a"},
            }
        ],
        snapshot_gateway_ids=["gw-a"],
        snapshot_pipeline_ids=["p"],
        timed_out=False,
    )
    assert verdict == "indeterminate"


def test_short_gateway_set_is_indeterminate_before_pipeline_diff():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1", "g2", "g3", "g4", "g5", "g6", "g7", "g8"],
        snapshot_pipeline_ids=["landed"],
        post_gateway_ids=["g1", "g2", "g3", "g4", "g5", "g6", "g7"],
        post_pipeline_ids=[],
        membership_seq=9,
        latest_catalog_seq=8,
        timed_out=False,
        land_paths=["pipelines/landed.yaml"],
        pipeline_sources={"landed": ["pipelines/landed.yaml"]},
    )
    assert verdict == "indeterminate"


def test_unrelated_absent_pipeline_is_not_fail_attributable():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["keep", "other"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=["keep"],
        membership_seq=4,
        latest_catalog_seq=3,
        timed_out=False,
        land_paths=["pipelines/keep.yaml"],
        pipeline_sources={
            "keep": ["pipelines/keep.yaml"],
            "other": ["pipelines/other.yaml"],
        },
    )
    assert verdict == "indeterminate"


def test_partial_membership_missing_unaffected_is_indeterminate_even_if_affected_absent():
    """Snapshot {A,B,C}, post {C}, land touches A → indeterminate."""
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["A", "B", "C"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=["C"],
        membership_seq=4,
        latest_catalog_seq=3,
        timed_out=False,
        land_paths=["pipelines/A.yaml"],
        pipeline_sources={
            "A": ["pipelines/A.yaml"],
            "B": ["pipelines/B.yaml"],
            "C": ["pipelines/C.yaml"],
        },
    )
    assert verdict == "indeterminate"


def test_deleted_yaml_pipeline_absent_is_pass():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["gone", "keep"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=["keep"],
        membership_seq=4,
        latest_catalog_seq=3,
        timed_out=False,
        land_paths=["pipelines/gone.yaml"],
        land_deleted_paths=["pipelines/gone.yaml"],
        pipeline_sources={
            "gone": ["pipelines/gone.yaml"],
            "keep": ["pipelines/keep.yaml"],
        },
    )
    assert verdict == "pass"


def test_deleted_yaml_pipeline_still_present_failing_op_run_is_fail_attributable():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["gone"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=["gone"],
        membership_seq=4,
        latest_catalog_seq=3,
        timed_out=False,
        land_paths=["pipelines/gone.yaml"],
        land_deleted_paths=["pipelines/gone.yaml"],
        pipeline_sources={"gone": ["pipelines/gone.yaml"]},
        op_run_failures=["gone"],
    )
    assert verdict == "fail_attributable"


def test_empty_source_list_is_not_expected_absent():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["ghost", "keep"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=["keep"],
        membership_seq=4,
        latest_catalog_seq=3,
        timed_out=False,
        land_paths=["pipelines/other.yaml"],
        land_deleted_paths=["pipelines/other.yaml"],
        pipeline_sources={"ghost": [], "keep": ["pipelines/keep.yaml"]},
    )
    assert verdict == "indeterminate"


def test_partial_delete_plus_modify_absent_is_fail_attributable():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["split"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=[],
        membership_seq=4,
        latest_catalog_seq=3,
        timed_out=False,
        land_paths=["pipelines/split.yaml", "pipelines/split-extra.yaml"],
        land_deleted_paths=["pipelines/split.yaml"],
        pipeline_sources={"split": ["pipelines/split.yaml", "pipelines/split-extra.yaml"]},
    )
    assert verdict == "fail_attributable"


def test_absent_pipeline_whose_source_is_in_the_diff_is_fail_attributable():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["keep"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=[],
        membership_seq=4,
        latest_catalog_seq=3,
        timed_out=False,
        land_paths=["pipelines/keep.yaml"],
        pipeline_sources={"keep": ["pipelines/keep.yaml"]},
    )
    assert verdict == "fail_attributable"


def test_op_run_failure_on_affected_pipeline_is_fail_attributable():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["keep"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=["keep"],
        membership_seq=4,
        latest_catalog_seq=None,
        timed_out=False,
        land_paths=["pipelines/keep.yaml"],
        pipeline_sources={"keep": ["pipelines/keep.yaml"]},
        op_run_failures=["keep"],
    )
    assert verdict == "fail_attributable"


def test_step_type_module_in_diff_marks_pipeline_affected():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["uses-step"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=[],
        membership_seq=2,
        latest_catalog_seq=None,
        timed_out=False,
        land_paths=["handlers/step_mod.py"],
        pipeline_sources={"uses-step": ["pipelines/uses-step.yaml"]},
        step_type_modules={"embed": "handlers/step_mod.py"},
        pipeline_step_types={"uses-step": ["embed"]},
    )
    assert verdict == "fail_attributable"


def test_unrelated_stargate_py_does_not_fail():
    verdict = judge_settle(
        snapshot_gateway_ids=["g1"],
        snapshot_pipeline_ids=["uses-step"],
        post_gateway_ids=["g1"],
        post_pipeline_ids=["uses-step"],
        membership_seq=2,
        latest_catalog_seq=None,
        timed_out=False,
        land_paths=["services/universal-stargate/systems/proxy/health_adjacent.py"],
        pipeline_sources={"uses-step": ["pipelines/uses-step.yaml"]},
        step_type_modules={"embed": "handlers/step_mod.py"},
        pipeline_step_types={"uses-step": ["embed"]},
    )
    assert verdict == "pass"
