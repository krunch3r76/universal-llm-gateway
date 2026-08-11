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
    base: dict[str, object] = {
        "service": "git_integration_worker",
        "code_ref": "abc1230000000000000000000000000000000000",
    }
    base.update(kwargs)
    # Flat ``code_version`` probes exercise process_live settlement — not giw's
    # default served_artifact predicate, which requires ``surfaces``.
    if "proof_class" not in kwargs and base.get("service") == "git_integration_worker":
        base["proof_class"] = "process_live"
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


def test_unguarded_unrelated_fails_deploy_line(tmp_path, monkeypatch) -> None:
    """Valid but unrelated ref → deploy-line fail (arc 6885 B); not eternal open."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    expected = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=expected)])
    row = list_open_rows()[0]

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": "other0000000000000000000000000000000000"}

    result = settle_open_row(row, probe)
    assert result.outcome == "failed"
    assert "deploy-line" in result.detail
    assert list_open_rows() == []


def test_guarded_unrelated_fails_deploy_line_without_uptime(
    tmp_path, monkeypatch
) -> None:
    """Deploy-line fail does not require generation attribution (arc 6885 B)."""
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
    assert result.outcome == "failed"
    assert list_open_rows() == []


def test_half_unreachable_composite_probe_is_indeterminate(
    tmp_path, monkeypatch
) -> None:
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
    assert "unevaluable" in result.detail.lower()
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
    # Valid 40-hex so env override + mint resolve both accept it.
    resolved = "abcdef0123456789abcdef0123456789abcdef01"
    monkeypatch.setenv("ULG_CODE_VERSION", resolved)
    reset_code_version_cache_for_tests()
    row_id = upsert_open_rows([_row(code_ref="HEAD")])[0]
    assert row_id == f"git_integration_worker:{resolved}:sync_restart"
    open_row = list_open_rows()[0]
    assert open_row.code_ref == resolved


def test_literal_head_row_terminal_fails_at_reconcile(tmp_path, monkeypatch) -> None:
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
    assert report["after_open"] == 0
    assert report["failed"] == 1
    assert report["unsettled"] == 0
    assert "HEAD" in report["results"][0].detail


def test_reconcile_before_after_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    sha_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    upsert_open_rows(
        [
            _row(service="git_integration_worker", code_ref=sha_a),
            _row(service="git_integration_worker", code_ref=sha_b),
        ]
    )

    def probe(service: str) -> dict[str, str] | None:
        if service == "git_integration_worker":
            return {"code_version": sha_a}
        return None

    report = reconcile_all_open_rows(probe)
    assert report["before_open"] == 2
    assert report["closed"] == 1
    # Unrelated resolvable tip is now a deploy-line fail (arc 6885 B), not
    # an eternal unsettled open.
    assert report["failed"] == 1
    assert report["after_open"] == 0


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


def test_exact_match_does_not_retire_on_process_live_pid_presence_alone(
    tmp_path, monkeypatch
) -> None:
    """Bypass name: process_live payload merely *having* pid must not retire.

    Isolates the former identity_attested presence shortcut: proof_passes is
    False and before is absent → indeterminate → close nothing.
    """
    from charter_runner_store.propagation_terminal_retire import (
        try_close_on_version_satisfaction,
    )

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=sha, proof_class="process_live")])
    row = list_open_rows()[0]
    payload = {"code_version": sha, "uptime_s": 2.0, "pid": 4242}

    detail = try_close_on_version_satisfaction(
        row,
        payload,
        proof_passes=False,
        determination="indeterminate",
        before=None,
    )
    assert detail is None
    assert len(list_open_rows()) == 1


def test_exact_match_retires_process_live_only_on_identity_delta(
    tmp_path, monkeypatch
) -> None:
    """Equal-ref identity arm closes only when proof_identity_attestation is changed."""
    from charter_runner_store.propagation_terminal_retire import (
        try_close_on_version_satisfaction,
    )

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=sha, proof_class="process_live")])
    row = list_open_rows()[0]
    before = {"code_version": sha, "pid": 100}
    after = {"code_version": sha, "uptime_s": 2.0, "pid": 200}

    unchanged = try_close_on_version_satisfaction(
        row,
        {"code_version": sha, "uptime_s": 2.0, "pid": 100},
        proof_passes=False,
        determination="indeterminate",
        before=before,
    )
    assert unchanged is None
    assert len(list_open_rows()) == 1

    detail = try_close_on_version_satisfaction(
        row,
        after,
        proof_passes=False,
        determination="indeterminate",
        before=before,
    )
    assert detail is not None
    assert "identity delta attested" in detail
    assert list_open_rows() == []


def test_mcp_client_visible_does_not_retire_on_matching_code_version_alone(
    tmp_path, monkeypatch
) -> None:
    """Bypass name: mcp client_visible + matching code_version, no identity move.

    Former mcp_surface_attested closed on version alone; must not retire.
    """
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    other = "def4560000000000000000000000000000000000"
    upsert_open_rows([_row(service="mcp", code_ref=sha, proof_class="client_visible")])
    row = list_open_rows()[0]

    def probe(_service: str) -> dict[str, object]:
        return {
            "mcp_health": {"code_version": sha, "pid": 1},
            "cortex_api": {"code_version": other, "pid": 2},
        }

    result = settle_open_row(row, probe, defer_if_unreachable=True)
    assert result.outcome != "closed"
    assert "mcp surface attested" not in (result.detail or "")
    assert len(list_open_rows()) == 1


def test_ancestry_satisfied_descendant_version_closes_not_failed(
    tmp_path, monkeypatch
) -> None:
    """Case (ii): newer live code retires the ancestor row (D2); must not fail it.

    Prior lock (defers_not_closed) protected: do not treat ancestry as proof of
    *this* SHA's deploy, and do not terminal-fail a superseded obligation.
    D2 keeps the no-fail half and retires the open row so satisfied debt stops
    presenting as unfired propagate — equal-ref identity proof stays separate.
    """
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
    assert result.outcome == "closed"
    assert result.outcome != "failed"
    assert result.outcome != "deferred"
    assert "ancestry satisfied" in result.detail
    assert list_open_rows() == []


def test_unrelated_sweep_fails_deploy_line_with_boundary(tmp_path, monkeypatch) -> None:
    """Defect B: resolvable unrelated tip fails deploy-line (not code_ref class)."""
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
    assert result.outcome == "failed"
    assert "deploy-line" in result.detail
    assert list_open_rows() == []


def test_reconcile_sweep_ancestry_row_closes(tmp_path, monkeypatch) -> None:
    """D2: sweep against ancestry-satisfied row retires it; must not fail."""
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
    assert report["closed"] == 1
    assert report["failed"] == 0
    assert report["after_open"] == 0


def test_client_visible_mcp_flat_payload_does_not_close_satisfied(
    tmp_path,
    monkeypatch,
) -> None:
    """76776b54-class: flat payload must not close client_visible mcp as satisfied."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "76776b540b00d9c704d5a231f93c70bdc0a78c2c"
    upsert_open_rows(
        [
            _row(
                service="mcp",
                code_ref=sha,
                proof_class="client_visible",
            ),
        ]
    )
    row = list_open_rows()[0]

    def flat_probe(_service: str) -> dict[str, str]:
        return {"code_version": sha}

    result = settle_open_row(row, flat_probe, defer_if_unreachable=True)
    assert result.outcome == "deferred"
    assert result.outcome != "closed"
    assert "unevaluable" in result.detail.lower()
    assert len(list_open_rows()) == 1
    assert list_open_rows()[0].defer_reason == "proof_unevaluable_payload_shape"


def test_served_artifact_flat_code_version_only_unevaluable(
    tmp_path,
    monkeypatch,
) -> None:
    """Open fork closed: flat code_version-only is unevaluable for served_artifact."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    upsert_open_rows(
        [
            _row(
                service="rag",
                code_ref=sha,
                proof_class="served_artifact",
            ),
        ]
    )
    row = list_open_rows()[0]

    def flat_probe(_service: str) -> dict[str, str]:
        return {"code_version": sha}

    result = settle_open_row(row, flat_probe, defer_if_unreachable=True)
    assert result.outcome == "deferred"
    assert "unevaluable" in result.detail.lower()
    assert list_open_rows()[0].defer_reason == "proof_unevaluable_payload_shape"


def test_served_artifact_proper_shape_passes_evaluable_gate(
    tmp_path,
    monkeypatch,
) -> None:
    """Same gate path: proper served_artifact shape is evaluable (not unevaluable)."""
    from deploy_identity.code_ref_relation import code_ref_relation_from_observed

    from charter_runner_store.propagation_determination import proof_evaluable

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    upsert_open_rows(
        [
            _row(
                service="rag",
                code_ref=sha,
                proof_class="served_artifact",
            ),
        ]
    )
    row = list_open_rows()[0]
    surface = {
        "url": "http://127.0.0.1/openapi.json",
        "x_mcp_count": 7,
        "bytes_sha256": "deadbeef",
        "bytes_len": 100,
    }
    payload = {
        "proof_class": "served_artifact",
        "surfaces": {"uds": surface},
        "byte_identical": True,
        "x_mcp_count": 7,
        "expected_x_mcp_count": 7,
        "code_version": None,
        "code_ref": sha,
        "code_ref_relation": code_ref_relation_from_observed(sha, None),
    }
    assert proof_evaluable(payload, proof_class="served_artifact")

    monkeypatch.setattr(
        "charter_runner_store.propagation_terminal._probe_for_projection",
        lambda _row: payload,
    )
    from charter_runner_store.propagation_terminal import default_probe

    result = settle_open_row(row, default_probe, defer_if_unreachable=True)
    assert "unevaluable" not in result.detail.lower()


def test_literal_head_terminal_fail_omits_recovered_code_ref(
    tmp_path, monkeypatch
) -> None:
    import json

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
            "mcp:HEAD:sync_restart",
            "mcp",
            "sync_restart",
            "HEAD",
            "drain_required",
            "probe",
            "process_live",
        ),
    )
    conn.commit()
    conn.close()
    row = list_open_rows()[0]
    calls: list[str] = []

    def _track_recover(stored: str) -> None:
        calls.append(stored)
        return None

    monkeypatch.setattr(
        "charter_runner_store.propagation_code_ref_mint.try_recover_code_ref",
        _track_recover,
    )
    result = settle_open_row(
        row, lambda _s: {"code_version": "x"}, defer_if_unreachable=True
    )

    assert result.outcome == "failed"
    assert calls == []
    stored = open_ledger_db(db).execute(
        "SELECT status, proof_payload FROM propagation_ledger WHERE row_id=?",
        (row.row_id,),
    ).fetchone()
    payload = json.loads(stored["proof_payload"])
    assert stored["status"] == "failed"
    assert "recovered_code_ref" not in payload


def test_literal_head_cannot_satisfied_close(tmp_path, monkeypatch) -> None:
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
            "mcp:HEAD:sync_restart",
            "mcp",
            "sync_restart",
            " head ",
            "drain_required",
            "probe",
            "process_live",
        ),
    )
    conn.commit()
    conn.close()
    row = list_open_rows()[0]
    sha = "abc1230000000000000000000000000000000000"

    result = settle_open_row(
        row, lambda _s: {"code_version": sha}, defer_if_unreachable=True
    )
    assert result.outcome == "failed"
    assert result.outcome != "closed"
    assert list_open_rows() == []


def test_cross_service_settle_does_not_touch_other_service_rows(
    tmp_path, monkeypatch
) -> None:
    from charter_runner_store.db import open_ledger_db

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    db = open_ledger_db()
    try:
        for service in ("mcp", "stargate"):
            db.execute(
                """
                INSERT INTO propagation_ledger (
                  row_id, service, action, code_ref, safe_window, proof, proof_class,
                  status, age_in_harvests, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0, 1.0, 1.0)
                """,
                (
                    f"{service}:{sha}:sync_restart",
                    service,
                    "sync_restart",
                    sha,
                    "drain_required",
                    "probe",
                    "process_live",
                ),
            )
        db.commit()
    finally:
        db.close()

    def probe(service: str) -> dict[str, str]:
        return {"code_version": sha}

    results = settle_open_rows_for_service(
        "mcp", probe, settle_not_before_monotonic=time.monotonic()
    )
    assert len(results) == 1
    assert results[0].service == "mcp"
    assert results[0].outcome == "closed"
    open_rows = list_open_rows()
    assert len(open_rows) == 1
    assert open_rows[0].service == "stargate"


def test_unreachable_defer_reason_ready_timeout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_row(code_ref="abc1230000000000000000000000000000000000")])
    row = list_open_rows()[0]

    result = settle_open_row(
        row,
        lambda _service: None,
        defer_if_unreachable=True,
        unreachable_defer_reason="proof_pending_ready_timeout",
    )
    assert result.outcome == "deferred"
    assert "ready-wait exhausted" in result.detail
    assert list_open_rows()[0].defer_reason == "proof_pending_ready_timeout"


def test_unreachable_defer_reason_after_drain_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_row(code_ref="abc1230000000000000000000000000000000000")])
    row = list_open_rows()[0]

    result = settle_open_row(
        row,
        lambda _service: None,
        defer_if_unreachable=True,
    )
    assert result.outcome == "deferred"
    assert list_open_rows()[0].defer_reason == "proof_pending_after_drain"


def test_ready_wait_defer_tokens_do_not_collapse() -> None:
    from charter_runner_store.propagation_terminal import (
        _DEFER_AFTER_DRAIN,
        _DEFER_READY_TIMEOUT,
        _DEFER_READY_WAIT,
    )

    tokens = (_DEFER_AFTER_DRAIN, _DEFER_READY_WAIT, _DEFER_READY_TIMEOUT)
    assert len(set(tokens)) == 3
    assert _DEFER_AFTER_DRAIN != _DEFER_READY_TIMEOUT
    assert _DEFER_READY_WAIT != _DEFER_READY_TIMEOUT

