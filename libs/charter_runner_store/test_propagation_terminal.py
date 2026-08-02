"""Unit tests for propagation ledger terminal settlement from observed proof."""

from __future__ import annotations

import time

from deploy_identity.code_version import reset_code_version_cache_for_tests
from implement_admission.propagation_row import PropagationRow

from charter_runner_store.propagation_ledger import (
    list_open_rows,
    upsert_open_rows,
)
from charter_runner_store.propagation_terminal import (
    _probe_is_outgoing_generation,
    reconcile_all_open_rows,
    settle_open_row,
    settle_open_rows_for_service,
)


def _row(**kwargs: object) -> PropagationRow:
    base = {
        "service": "git_integration_worker",
        "code_ref": "abc1230000000000000000000000000000000000",
    }
    base.update(kwargs)
    return PropagationRow(**base)


def test_queued_row_closes_on_matching_probe(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=sha)])
    open_before = list_open_rows()
    assert len(open_before) == 1

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": sha}

    result = settle_open_row(open_before[0], probe)
    assert result.outcome == "closed"
    assert list_open_rows() == []


def test_unguarded_mismatch_leaves_row_open(tmp_path, monkeypatch) -> None:
    """No restart boundary ⇒ a mismatch cannot be attributed to the new generation."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    expected = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=expected)])
    row = list_open_rows()[0]

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": "other0000000000000000000000000000000000"}

    result = settle_open_row(row, probe)
    assert result.outcome == "unsettled"
    assert "unrelated or unresolvable" in result.detail
    assert len(list_open_rows()) == 1


def test_guarded_mismatch_without_uptime_is_not_terminal(tmp_path, monkeypatch) -> None:
    """A boundary alone is not enough — without ``uptime_s`` the reading is unattributable."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    expected = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=expected)])
    row = list_open_rows()[0]

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": "other0000000000000000000000000000000000"}

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=time.monotonic() - 30.0,
    )
    assert result.outcome == "deferred"
    assert len(list_open_rows()) == 1


def test_half_unreachable_composite_probe_is_indeterminate(tmp_path, monkeypatch) -> None:
    """One dead half of an mcp composite payload must not read as a mismatch."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    expected = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(service="mcp", code_ref=expected)])
    row = list_open_rows()[0]

    def probe(_service: str) -> dict[str, object]:
        return {"mcp_health": {"code_version": expected}, "cortex_api": None}

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=time.monotonic() - 30.0,
    )
    assert result.outcome == "deferred"
    assert "no readable code_version" in result.detail
    assert len(list_open_rows()) == 1


def test_settle_is_idempotent_on_second_pass(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "deadbeef00000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=sha)])

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": sha}

    first = settle_open_rows_for_service("git_integration_worker", probe)
    assert len(first) == 1
    assert first[0].outcome == "closed"
    second = settle_open_rows_for_service("git_integration_worker", probe)
    assert second == []
    assert list_open_rows() == []


def test_head_resolves_at_mint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    resolved = "resolved0000000000000000000000000000000000"
    monkeypatch.setenv("ULG_CODE_VERSION", resolved)
    reset_code_version_cache_for_tests()
    row_id = upsert_open_rows([_row(code_ref="HEAD")])[0]
    assert row_id == f"git_integration_worker:{resolved}:sync_restart"
    open_row = list_open_rows()[0]
    assert open_row.code_ref == resolved


def test_literal_head_row_unsettled_at_reconcile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    db = tmp_path / "root-ledger.sqlite"
    from charter_runner_store.db import open_ledger_db

    conn = open_ledger_db(db)
    conn.execute(
        """
        INSERT INTO propagation_ledger (
          row_id, service, action, code_ref, safe_window, proof, proof_class,
          status, age_in_harvests, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0, 1.0, 1.0)
        """,
        (
            "git_integration_worker:HEAD:sync_restart",
            "git_integration_worker",
            "sync_restart",
            "HEAD",
            "drain_required",
            "probe",
            "process_live",
        ),
    )
    conn.commit()
    conn.close()

    report = reconcile_all_open_rows(lambda _s: {"code_version": "anything"})
    assert report["before_open"] == 1
    assert report["after_open"] == 1
    assert report["unsettled"] == 1
    assert "HEAD" in report["results"][0].detail


def test_reconcile_before_after_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    sha_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    upsert_open_rows(
        [
            _row(service="mcp", code_ref=sha_a),
            _row(service="mcp", code_ref=sha_b),
        ]
    )

    def probe(service: str) -> dict[str, str] | None:
        if service == "mcp":
            return {"code_version": sha_a}
        return None

    report = reconcile_all_open_rows(probe)
    assert report["before_open"] == 2
    assert report["closed"] == 1
    # The unmatched row is NOT failed: an unguarded sweep cannot tell a genuine
    # mismatch from a probe of the outgoing generation, and fail_row is terminal.
    assert report["failed"] == 0
    assert report["unsettled"] == 1
    assert report["after_open"] == 1


def test_outgoing_generation_probe_defers_on_mismatch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    expected = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=expected)])
    row = list_open_rows()[0]
    settle_not_before = time.monotonic()

    def probe(_service: str) -> dict[str, float | str]:
        return {
            "code_version": "other0000000000000000000000000000000000",
            "uptime_s": 600.0,
        }

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=settle_not_before,
    )
    assert result.outcome == "deferred"
    assert "outgoing generation" in result.detail
    assert len(list_open_rows()) == 1


def test_genuine_post_restart_stale_code_fails(tmp_path, monkeypatch) -> None:
    """Stale observed code (older than row target) fails when attribution holds."""
    import subprocess

    from universal_workspace import get_workspace_root

    root = get_workspace_root()
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ancestor = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_row(code_ref=head)])
    row = list_open_rows()[0]
    settle_not_before = time.monotonic() - 30.0

    def probe(_service: str) -> dict[str, float | str]:
        return {
            "code_version": ancestor,
            "uptime_s": 2.0,
            "pid": 4242,
        }

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=settle_not_before,
    )
    assert result.outcome == "failed"
    assert "stale code" in result.detail
    assert list_open_rows() == []


def test_matching_post_restart_probe_closes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=sha)])
    row = list_open_rows()[0]
    settle_not_before = time.monotonic() - 30.0

    def probe(_service: str) -> dict[str, float | str]:
        return {"code_version": sha, "uptime_s": 2.0}

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=settle_not_before,
    )
    assert result.outcome == "closed"
    assert list_open_rows() == []


def test_probe_is_outgoing_generation_guard_condition() -> None:
    settle_not_before = time.monotonic()
    assert _probe_is_outgoing_generation(
        {"uptime_s": 600.0}, settle_not_before_monotonic=settle_not_before
    )
    assert not _probe_is_outgoing_generation(
        {"uptime_s": 2.0}, settle_not_before_monotonic=settle_not_before - 30.0
    )
    assert not _probe_is_outgoing_generation(
        {"code_version": "sha"}, settle_not_before_monotonic=settle_not_before
    )


def test_ac17_completion_boundary_prevents_first_pass_close(
    tmp_path, monkeypatch
) -> None:
    """Regression: production passed completion monotonic — rows never close on first settle."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=sha)])
    row = list_open_rows()[0]
    drain_start_boundary = time.monotonic() - 30.0
    completion_boundary = time.monotonic()

    def probe(_service: str) -> dict[str, float | int | str]:
        return {"code_version": sha, "uptime_s": 2.0, "pid": 526100}

    broken = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=completion_boundary,
    )
    assert broken.outcome == "deferred"
    assert "outgoing generation" in broken.detail
    assert len(list_open_rows()) == 1

    fixed = settle_open_row(
        list_open_rows()[0],
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=drain_start_boundary,
    )
    assert fixed.outcome == "closed"
    assert list_open_rows() == []


def test_reconcile_uses_persisted_row_boundary(tmp_path, monkeypatch) -> None:
    """Deferred rows reconcile with stored boundary — no wall-clock re-derivation."""
    from charter_runner_store.propagation_ledger import set_settle_boundary

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=sha)])
    row = list_open_rows()[0]
    drain_start_boundary = time.monotonic() - 30.0
    set_settle_boundary(row.row_id, drain_start_boundary)

    def probe(_service: str) -> dict[str, float | int | str]:
        return {"code_version": sha, "uptime_s": 2.0, "pid": 526100}

    report = reconcile_all_open_rows(probe)
    assert report["closed"] == 1
    assert report["after_open"] == 0


def test_ancestry_satisfied_descendant_version_defers_not_closed(
    tmp_path, monkeypatch
) -> None:
    """Case (ii): newer live code must not close or fail the row."""
    import subprocess

    from charter_runner_store.propagation_version_satisfaction import (
        DEFER_ANCESTRY_SATISFIED,
    )
    from universal_workspace import get_workspace_root

    root = get_workspace_root()
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ancestor = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_row(code_ref=ancestor)])
    row = list_open_rows()[0]
    settle_not_before = time.monotonic() - 30.0

    def probe(_service: str) -> dict[str, float | int | str]:
        return {"code_version": head, "uptime_s": 2.0, "pid": 9001}

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=settle_not_before,
    )
    assert result.outcome == "deferred"
    assert result.outcome != "closed"
    assert result.outcome != "failed"
    assert "ancestry satisfied" in result.detail
    refreshed = list_open_rows()[0]
    assert refreshed.defer_reason == DEFER_ANCESTRY_SATISFIED


def test_unrelated_sweep_does_not_fail_with_boundary(tmp_path, monkeypatch) -> None:
    """Case (iii): unrelated ref must not terminally fail even with attribution."""
    from charter_runner_store.propagation_version_satisfaction import (
        DEFER_UNRELATED_OR_UNRESOLVABLE,
    )

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    expected = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=expected)])
    row = list_open_rows()[0]
    settle_not_before = time.monotonic() - 30.0

    def probe(_service: str) -> dict[str, float | str]:
        return {
            "code_version": "other0000000000000000000000000000000000",
            "uptime_s": 2.0,
            "pid": 9002,
        }

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=settle_not_before,
    )
    assert result.outcome == "deferred"
    assert result.outcome != "failed"
    assert len(list_open_rows()) == 1
    assert list_open_rows()[0].defer_reason == DEFER_UNRELATED_OR_UNRESOLVABLE


def test_reconcile_sweep_ancestry_row_stays_open(tmp_path, monkeypatch) -> None:
    """Sweep against ancestry-satisfied row must not produce closed or failed."""
    import subprocess

    from universal_workspace import get_workspace_root

    root = get_workspace_root()
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ancestor = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_row(code_ref=ancestor)])

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": head}

    report = reconcile_all_open_rows(probe)
    assert report["closed"] == 0
    assert report["failed"] == 0
    assert report["after_open"] == 1
