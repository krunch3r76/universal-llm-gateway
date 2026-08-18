"""Hermetic tests for commit-to-activation attribution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from charter_runner_store import propagation_validation
from charter_runner_store.propagation_liveness import CodeRefLiveness
from charter_runner_store.propagation_validation.lifecycle import (
    mint_pending_validation_for_intent,
)
from charter_runner_store.propagation_validation.model import store_code_ref
from charter_runner_store.propagation_validation.queries import (
    bind_validation_to_row,
    get_validation,
)

_ROW_SHA = "8fc646c70" + "a" * 31
_FOREIGN_ROW = "98e6222d-foreign"
_NEW_ROW = "row-new-propagate"


def _mute_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.propagation_probe.probe_process_live",
        lambda _service: {"probe_reachable": False},
    )


def _live_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "charter_runner_store.propagation_validation.close.observe_code_ref_live",
        lambda service, code_ref: CodeRefLiveness(
            answer="yes",
            service=service,
            code_ref=code_ref,
            observed_code_version=code_ref,
            relation="equal",
            observation={"probe_reachable": True},
            reason="test",
        ),
    )


def test_validation_record_and_current_projection(monkeypatch, tmp_path):
    """Recorded validation projects to current running_committed_code when live probe matches."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    validation_id = propagation_validation.record_validation(
        service="agent_bus",
        code_ref="a" * 40,
        row_id="row-1",
        pre_observation={"pid": 1},
        post_observation={"pid": 2, "code_version": "a" * 40},
        observed_code_version="a" * 40,
        code_ref_relation="equal",
        identity_measurement="changed",
        outcome="validated",
    )

    assert validation_id
    monkeypatch.setattr(
        "charter_runner_store.propagation_validation.close.observe_code_ref_live",
        lambda service, code_ref: CodeRefLiveness(
            answer="yes",
            service=service,
            code_ref=code_ref,
            observed_code_version=code_ref,
            relation="equal",
            observation={"probe_reachable": True},
            reason="test",
        ),
    )
    result = propagation_validation.current_validation("agent_bus", "a" * 40)
    assert result["verdict"] == "running_committed_code"
    assert result["activation"]["validation_id"] == validation_id


def test_unknown_probe_never_promotes_stale_record(monkeypatch, tmp_path):
    """Unknown live probe keeps current_validation at unknown instead of promoting stale validated record."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    propagation_validation.record_validation(
        service="agent_bus",
        code_ref="b" * 40,
        outcome="validated",
        identity_measurement="changed",
    )
    monkeypatch.setattr(
        "charter_runner_store.propagation_validation.close.observe_code_ref_live",
        lambda service, code_ref: CodeRefLiveness(
            answer="unknown",
            service=service,
            code_ref=code_ref,
            observed_code_version=None,
            relation=None,
            observation={"probe_reachable": False},
            reason="unreachable",
        ),
    )
    assert (
        propagation_validation.current_validation("agent_bus", "b" * 40)["verdict"]
        == "unknown"
    )


@pytest.mark.offline
def test_mint_stores_propagate_row_sha_not_head(monkeypatch, tmp_path) -> None:
    """A new mint keyed to the row SHA stores that SHA, not process HEAD."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    _mute_probe(monkeypatch)
    intent = SimpleNamespace(service="git_integration_worker", intent_id="intent-new")
    validation_id = mint_pending_validation_for_intent(intent, code_ref=_ROW_SHA)
    record = get_validation(validation_id)
    assert record is not None
    assert record.code_ref == _ROW_SHA
    head_sha = store_code_ref("HEAD", service="git_integration_worker")
    assert record.code_ref != head_sha


@pytest.mark.offline
def test_mint_refuses_occupied_pending_5139a3e6_shape(monkeypatch, tmp_path) -> None:
    """Occupied pending (row_id already set) is not reused; bind of the new id succeeds."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    _mute_probe(monkeypatch)
    occupied_id = propagation_validation.record_validation(
        service="git_integration_worker",
        code_ref=_ROW_SHA,
        row_id=_FOREIGN_ROW,
        restart_intent="intent-old",
        outcome="pending",
    )
    intent = SimpleNamespace(service="git_integration_worker", intent_id="intent-new")
    new_id = mint_pending_validation_for_intent(
        intent, code_ref=_ROW_SHA, row_id=_NEW_ROW
    )
    assert new_id != occupied_id
    occupied = get_validation(occupied_id)
    assert occupied is not None
    assert occupied.row_id == _FOREIGN_ROW
    assert occupied.outcome == "superseded"
    fresh = get_validation(new_id)
    assert fresh is not None
    assert fresh.row_id is None
    assert fresh.code_ref == _ROW_SHA
    assert bind_validation_to_row(new_id, _NEW_ROW) == 1
    assert bind_validation_to_row(occupied_id, _NEW_ROW) == 0


@pytest.mark.offline
def test_head_lookup_does_not_return_foreign_bound_validation(
    monkeypatch, tmp_path
) -> None:
    """HEAD-keyed projection must not attribute a validation bound to another row."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    _live_yes(monkeypatch)
    head_sha = store_code_ref("HEAD", service="git_integration_worker")
    occupied_id = propagation_validation.record_validation(
        service="git_integration_worker",
        code_ref=head_sha,
        row_id=_FOREIGN_ROW,
        outcome="pending",
    )
    unbound_id = propagation_validation.record_validation(
        service="git_integration_worker",
        code_ref=_ROW_SHA,
        outcome="pending",
    )
    head_result = propagation_validation.current_validation(
        "git_integration_worker", "HEAD"
    )
    assert head_result["verdict"] == "activation_unattributed"
    assert head_result["activation"] is None
    identity = propagation_validation.current_validation(
        "git_integration_worker",
        "HEAD",
        activation_validation_id=unbound_id,
    )
    assert identity["activation"] is not None
    assert identity["activation"]["validation_id"] == unbound_id
    by_sha = propagation_validation.current_validation(
        "git_integration_worker", head_sha
    )
    assert by_sha["activation"] is not None
    assert by_sha["activation"]["validation_id"] == occupied_id
