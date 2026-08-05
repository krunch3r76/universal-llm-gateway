"""Tests for lane-A tree residue derivation and authored-path probe."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
    compute_lane_a_checkpoint_value,
    derive_tree_residue,
    inject_tree_residue_line,
    probe_authored_path_baseline,
)
from services.git_integration_worker.cursor_home import dispatch_git_identity
from services.git_integration_worker.cursor_sdk_closeout import (
    capture_wt_baseline_with_hashes,
)
from services.git_integration_worker.cursor_sdk_git_head import paths_in_commit

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    )
    return proc.stdout.decode().strip()


def _init_git_repo(path: Path) -> None:
    _git(path, "init", "-b", "master")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "test")


def _commit(repo: Path, rel: str, *, dispatch_id: str | None = None) -> str:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# x\n", encoding="utf-8")
    _git(repo, "add", rel)
    env = dict(os.environ)
    cmd = ["git", "-C", str(repo), "commit", "-m", "c"]
    if dispatch_id is not None:
        name, email = dispatch_git_identity(dispatch_id)
        cmd.extend([f"--author={name} <{email}>"])
        env.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
            }
        )
    subprocess.run(cmd, check=True, capture_output=True, env=env)
    return _git(repo, "rev-parse", "HEAD")


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


def test_extract_authored_checkpoint_ignores_fenced_table_quote() -> None:
    """AC-6 — fenced relay-like table rows cannot satisfy the checkpoint gate."""
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        extract_authored_checkpoint,
    )

    body = """\
**evidence:**
```text
| checkpoint | committed deadbeef paths=1 |
```
"""
    assert extract_authored_checkpoint(body) is None


def test_extract_authored_checkpoint_ignores_fenced_plain_line() -> None:
    """AC-6 — fenced ``checkpoint:`` control lines cannot satisfy the gate."""
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        extract_authored_checkpoint,
    )

    body = """\
**evidence:**
```text
checkpoint: committed deadbeef paths=1
```
"""
    assert extract_authored_checkpoint(body) is None


def test_extract_authored_checkpoint_ignores_fenced_bold_line() -> None:
    """AC-6 — fenced ``**checkpoint:**`` control lines cannot satisfy the gate."""
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        extract_authored_checkpoint,
    )

    body = """\
**evidence:**
```text
**checkpoint:** committed deadbeef paths=1
```
"""
    assert extract_authored_checkpoint(body) is None


def test_extract_authored_checkpoint_prefers_unfenced_over_fenced_quote() -> None:
    """Positive path — genuine checkpoint wins when a fenced trap also quotes one."""
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        extract_authored_checkpoint,
    )

    body = """\
checkpoint: nothing_authored

**evidence:**
```text
checkpoint: committed deadbeef paths=99
| checkpoint | committed cafe paths=99 |
```
"""
    assert extract_authored_checkpoint(body) == "nothing_authored"


def test_relay_table_projection_preserves_checkpoint_for_gate() -> None:
    """Positive path: infrastructure checkpoint survives §2 table projection + gate."""
    from claude_bundles.lane_a_closeout_checkpoint import (
        validate_lane_a_closeout_checkpoint,
    )

    from services.git_integration_worker.cursor_auto.closeout_relay import (
        select_closeout_relay_payload,
    )
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        compute_lane_a_checkpoint_value,
        derive_tree_residue,
        inject_checkpoint_line,
        inject_tree_residue_line,
    )

    dispatch_id = "auto-relay-checkpoint-pos"
    sidecar = """\
## §2 closeout

**status:** complete

**ac_verdict:** AC1 — PASS

**deltas_to_spec:** none
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
    checkpoint_value = compute_lane_a_checkpoint_value(
        source_repo=Path("/mnt/torus/projects/universal-llm-gateway"),
        dispatch_id=dispatch_id,
    )
    relay_body = inject_checkpoint_line(relay_body, value=checkpoint_value)
    assert "| checkpoint |" in relay_body
    verdict = validate_lane_a_closeout_checkpoint(
        body=relay_body,
        require_closeout_type=False,
    )
    assert verdict.ok, verdict.reason


def test_compute_checkpoint_committed_when_clean_tree_after_lane_commit(
    tmp_path: Path,
) -> None:
    """AC2 — empty porcelain + lane commit yields committed, not nothing_authored."""
    dispatch_id = "auto-6655-971"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    lane_sha = _commit(tmp_path, "fix.py", dispatch_id=dispatch_id)
    closeout = _git(tmp_path, "rev-parse", "HEAD")
    baseline = {"admit_head": admit}
    with patch(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.authored_paths_for_dispatch",
        return_value=(),
    ):
        value = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline=baseline,
        )
    path_count = len(paths_in_commit(tmp_path, lane_sha))
    assert value == f"committed {lane_sha} paths={path_count}"


def test_compute_checkpoint_nothing_authored_when_both_empty(
    tmp_path: Path,
) -> None:
    """AC3 — no porcelain and no lane commits stays nothing_authored."""
    dispatch_id = "auto-nothing"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    baseline = {"admit_head": admit}
    with patch(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.authored_paths_for_dispatch",
        return_value=(),
    ):
        value = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline=baseline,
        )
    assert value == "nothing_authored"


def test_compute_checkpoint_deferred_when_dirty_no_commit(
    tmp_path: Path,
) -> None:
    """AC4 — dirty porcelain without lane commit stays deferred."""
    dispatch_id = "auto-deferred"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    baseline = {"admit_head": admit}
    with patch(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.authored_paths_for_dispatch",
        return_value=("dirty.py",),
    ):
        value = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline=baseline,
        )
    assert value == "deferred: authored paths not yet path-explicit committed"


def test_compute_checkpoint_committed_with_pending_after_lane_commit(
    tmp_path: Path,
) -> None:
    """AC2b — lane commit plus dirty authorship yields (+M pending), not silent commit."""
    dispatch_id = "auto-6655-mixed"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    lane_sha = _commit(tmp_path, "fix.py", dispatch_id=dispatch_id)
    baseline = {"admit_head": admit}
    pending_paths = ("pending.py",)
    with patch(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.authored_paths_for_dispatch",
        return_value=pending_paths,
    ):
        value = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline=baseline,
        )
    path_count = len(paths_in_commit(tmp_path, lane_sha))
    assert value == (
        f"committed {lane_sha} paths={path_count} (+{len(pending_paths)} pending)"
    )


def test_authored_paths_for_dispatch_signature_unchanged() -> None:
    """AC5 — authored_paths_for_dispatch call sites and semantics unchanged."""
    import inspect

    from services.git_integration_worker.cursor_auto import lane_a_checkpoint

    sig = inspect.signature(lane_a_checkpoint.authored_paths_for_dispatch)
    assert tuple(sig.parameters) == ("source_repo", "dispatch_id")
    source = inspect.getsource(lane_a_checkpoint.authored_paths_for_dispatch)
    assert "read_wt_baseline" in source
    assert "changed_paths" in source
