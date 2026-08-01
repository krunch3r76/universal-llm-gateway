"""Tests for lane-A tree residue derivation and authored-path probe."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
    derive_tree_residue,
    inject_tree_residue_line,
    probe_authored_path_baseline,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    capture_wt_baseline_with_hashes,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_probe_records_baseline_limits() -> None:
    probe = probe_authored_path_baseline()
    assert probe.exact_at_dispatch is True
    assert probe.covers_nested_cursor_sdk is True
    assert probe.covers_attended_composer is True
    assert "register_seat_write" in probe.registration_mechanism
    assert "registration gaps" in probe.detail


def test_tree_residue_counts_only_registration_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    SeatWriteLedger.reset_instance()
    db = SeatWriteLedger(db_path=tmp_path.parent / "seat-write-ledger.db")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.SeatWriteLedger.instance",
        lambda: db,
    )
    _git(tmp_path, "init", "-b", "master")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "commit", "--allow-empty", "-m", "seed")
    # Registration gap: dirty at admit baseline, untouched during the episode.
    (tmp_path / "gap.py").write_text("g=dirty\n", encoding="utf-8")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    (tmp_path / "dispatch.py").write_text("d=1\n", encoding="utf-8")
    (tmp_path / "registered.py").write_text("r=1\n", encoding="utf-8")
    db.register_paths(
        arc_id="arc-1",
        seat_id="ide-composer",
        source_repo=str(tmp_path),
        paths=("registered.py",),
    )
    residue = derive_tree_residue(
        source_repo=tmp_path,
        dispatch_id="unused",
        baseline=baseline,
    )
    assert residue.count == 1


def test_inject_tree_residue_replaces_existing_line() -> None:
    body = "TYPE: CLOSEOUT\nstatus: complete\ntree_residue: 99\n"
    out = inject_tree_residue_line(body, count=3)
    assert "tree_residue: 3" in out
    assert "tree_residue: 99" not in out


def test_extract_authored_checkpoint_from_bold_and_plain() -> None:
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        extract_authored_checkpoint,
        inject_checkpoint_line,
    )

    bold = (
        "**checkpoint:** deferred: lane-B implement — sweeper owns commit\n"
        "**status:** complete\n"
    )
    assert extract_authored_checkpoint(bold) == (
        "deferred: lane-B implement — sweeper owns commit"
    )
    plain = "checkpoint: nothing_authored\n"
    assert extract_authored_checkpoint(plain) == "nothing_authored"
    injected = inject_checkpoint_line(
        "TYPE: CLOSEOUT\nstatus: complete\ntree_residue: 2\n",
        value="deferred: foreign WIP",
    )
    assert "checkpoint: deferred: foreign WIP" in injected


def test_relay_table_projection_preserves_checkpoint_for_gate() -> None:
    """Positive path: authored checkpoint survives §2 table projection + gate."""
    from claude_bundles.lane_a_closeout_checkpoint import (
        validate_lane_a_closeout_checkpoint,
    )

    from services.git_integration_worker.cursor_auto.closeout_relay import (
        select_closeout_relay_payload,
    )
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        derive_tree_residue,
        extract_authored_checkpoint,
        inject_checkpoint_line,
        inject_tree_residue_line,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )

    dispatch_id = "auto-relay-checkpoint-pos"
    sidecar = """\
## §2 closeout

**status:** complete

**ac_verdict:** AC1 — PASS

**deltas_to_spec:** none

**checkpoint:** deferred: path-explicit commit owned by quiescent sweeper
"""
    payload = select_closeout_relay_payload(
        sdk_body='{"status":"complete","schema_version":1}',
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id=dispatch_id,
    )
    residue = derive_tree_residue(
        source_repo=Path("/mnt/torus/projects/universal-llm-gateway"),
        dispatch_id=dispatch_id,
    )
    relay_body = inject_tree_residue_line(payload.body, count=residue.count)
    checkpoint_value = extract_authored_checkpoint(strip_machine_tail(sidecar))
    assert checkpoint_value is not None
    relay_body = inject_checkpoint_line(relay_body, value=checkpoint_value)
    assert "| checkpoint |" in relay_body
    verdict = validate_lane_a_closeout_checkpoint(
        body=relay_body,
        require_closeout_type=False,
    )
    assert verdict.ok, verdict.reason
