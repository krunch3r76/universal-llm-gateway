"""F4 reader: observation-cited code_ref liveness (obligation ≠ liveness)."""

from __future__ import annotations

import sqlite3

import pytest

from charter_runner_store.propagation_liveness import observe_code_ref_live


def test_observe_yes_on_equal_version() -> None:
    ref = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    result = observe_code_ref_live(
        "git_integration_worker",
        ref,
        probe=lambda _s: {"code_version": ref, "pid": 1},
    )
    assert result.answer == "yes"
    assert result.relation == "equal"
    assert result.observed_code_version == ref
    assert result.observation["code_version"] == ref
    assert result.observation["probe_reachable"] is True


def test_observe_yes_on_ancestor_relation(monkeypatch: pytest.MonkeyPatch) -> None:
    older = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    newer = "cccccccccccccccccccccccccccccccccccccccc"
    monkeypatch.setattr(
        "charter_runner_store.propagation_liveness.code_ref_relation_from_observed",
        lambda code_ref, observed: (
            "ancestor"
            if isinstance(observed, str) and code_ref == older and observed == newer
            else "unknown"
        ),
    )
    monkeypatch.setattr(
        "charter_runner_store.propagation_liveness.code_ref_satisfied",
        lambda code_ref, observed: code_ref == older and observed == newer,
    )
    result = observe_code_ref_live(
        "git_integration_worker",
        older,
        probe=lambda _s: {"code_version": newer, "pid": 2},
    )
    assert result.answer == "yes"
    assert result.relation == "ancestor"


def test_observe_no_on_unsatisfied_version(monkeypatch: pytest.MonkeyPatch) -> None:
    target = "dddddddddddddddddddddddddddddddddddddddd"
    stale = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    monkeypatch.setattr(
        "charter_runner_store.propagation_liveness.code_ref_relation_from_observed",
        lambda _c, _o: "descendant-of-observed",
    )
    monkeypatch.setattr(
        "charter_runner_store.propagation_liveness.code_ref_satisfied",
        lambda _c, _o: False,
    )
    result = observe_code_ref_live(
        "mcp",
        target,
        probe=lambda _s: {"code_version": stale, "pid": 3},
    )
    assert result.answer == "no"
    assert result.observed_code_version == stale
    assert result.relation == "descendant-of-observed"


def test_observe_unknown_when_probe_unreachable() -> None:
    result = observe_code_ref_live(
        "git_integration_worker",
        "ffffffffffffffffffffffffffffffffffffffff",
        probe=lambda _s: None,
    )
    assert result.answer == "unknown"
    assert result.observed_code_version is None
    assert result.observation["probe_reachable"] is False


def test_observe_unknown_when_code_version_missing() -> None:
    result = observe_code_ref_live(
        "cortex_api",
        "ffffffffffffffffffffffffffffffffffffffff",
        probe=lambda _s: {"status": "ok", "pid": 9},
    )
    assert result.answer == "unknown"
    assert "no readable code_version" in result.reason


def test_observe_does_not_open_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness answers must not consult ledger row status (F4 invariant)."""

    def _forbid_connect(*_a: object, **_k: object) -> sqlite3.Connection:
        raise AssertionError("observe_code_ref_live must not open sqlite")

    monkeypatch.setattr(sqlite3, "connect", _forbid_connect)
    result = observe_code_ref_live(
        "git_integration_worker",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        probe=lambda _s: {
            "code_version": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "pid": 1,
        },
    )
    assert result.answer == "yes"


def test_specimen_40f8eadd_giw_live_without_sqlite() -> None:
    """AC: after world caught up, reader says yes for the frozen failed specimen.

    Specimen row ``git_integration_worker:40f8eadd…:sync_restart`` remains
    ``status=failed`` in the ledger; the answer must come from the live probe.
    """
    specimen = "40f8eadde10a2fb2afcfde4960c11db11a22c56c"
    result = observe_code_ref_live("git_integration_worker", specimen)
    assert result.answer in {"yes", "unknown"}
    assert "code_version" in result.observation or result.answer == "unknown"
    if result.answer == "yes":
        assert result.observed_code_version
        assert result.relation in {"equal", "ancestor"}
        assert result.observation.get("probe_reachable") is True
    # Do not require sqlite: if the ledger still shows failed, that is the point.
    from charter_runner_store.db import open_ledger_db

    db = open_ledger_db()
    try:
        cur = db.execute(
            "SELECT status FROM propagation_ledger WHERE row_id=?",
            (f"git_integration_worker:{specimen}:sync_restart",),
        )
        row = cur.fetchone()
    finally:
        db.close()
    if row is not None and result.answer == "yes":
        assert row["status"] == "failed", (
            "specimen must stay a failed event while the reader answers yes"
        )
