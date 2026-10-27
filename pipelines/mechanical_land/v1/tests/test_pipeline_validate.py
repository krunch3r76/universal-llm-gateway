"""Pipeline validate + commit handler tests."""

from __future__ import annotations

from unittest import mock

import pytest

from pipelines.mechanical_land.v1.handlers.commit import (
    evaluate_commit,
    giw_mechanical_land_runs_url,
)
from pipelines.mechanical_land.v1.handlers.validate import evaluate_validate

pytestmark = pytest.mark.offline


def test_missing_files_expected_refused() -> None:
    out = evaluate_validate(
        {
            "op": "start",
            "kind": "implement",
            "source_ref": "todo:mechanical-land-pipeline-s2",
            "todo": {"acceptance_criteria": ["ac1"]},
        }
    )
    assert out["admitted"] is False
    assert out["code"] == "source_ref_unsettled"


def test_unknown_kind_422() -> None:
    out = evaluate_validate({"op": "start", "kind": "unknown"})
    assert out["admitted"] is False
    assert out["status_code"] == 422


def test_policy_unknown_key_refused() -> None:
    out = evaluate_validate(
        {
            "op": "start",
            "kind": "implement",
            "source_ref": "todo:x",
            "files_expected": ["a.py"],
            "acceptance_criteria": ["ac"],
            "policy": {"not_a_real_key": True},
        }
    )
    assert out["admitted"] is False
    assert out["code"] == "policy_invalid"


def test_commit_noop_when_op_transition_no_http() -> None:
    with mock.patch(
        "pipelines.mechanical_land.v1.handlers.commit.post_mechanical_land_run"
    ) as post:
        out = evaluate_commit(
            {"op": "transition"},
            {"admitted": True},
        )
        post.assert_not_called()
    assert out.get("http") is False


def test_giw_runs_url_uses_config_port_not_8765() -> None:
    with mock.patch(
        "pipelines.mechanical_land.v1.handlers.commit.load_config",
        return_value=mock.Mock(host="127.0.0.1", port=8091),
    ):
        url = giw_mechanical_land_runs_url()
    assert url == "http://127.0.0.1:8091/api/v1/mechanical-land/runs"
    assert "8765" not in url


def test_commit_posts_when_op_start() -> None:
    with mock.patch(
        "pipelines.mechanical_land.v1.handlers.commit.post_mechanical_land_run",
        return_value={"run_id": "ml-abc"},
    ) as post:
        out = evaluate_commit(
            {"op": "start"},
            {"admitted": True, "kind": "implement"},
        )
        post.assert_called_once()
    assert out.get("http") is True
