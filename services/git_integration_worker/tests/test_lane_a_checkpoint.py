"""Tests for lane-A tree residue derivation and authored-path probe."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_bundles.lane_a_closeout_checkpoint import (
    validate_lane_a_closeout_checkpoint,
)

from services.git_integration_worker.cursor_auto.closeout_tree_state import (
    compute_closeout_tree_state,
)
from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
    compute_lane_a_checkpoint_value,
    degraded_reason_from_closeout_wrapper,
    derive_tree_residue,
    inject_checkpoint_line,
    inject_tree_residue_line,
    null_run_suppresses_lane_a_authorship,
    probe_authored_path_baseline,
    rehash_cortex_uri,
)
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_home import dispatch_git_identity
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
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


def test_extract_checkpoint_claim_from_bold_and_plain() -> None:
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        extract_checkpoint_claim,
        inject_checkpoint_line,
    )

    bold = (
        "**checkpoint_claim:** deferred: lane-B implement — sweeper owns commit\n"
        "**status:** complete\n"
    )
    assert extract_checkpoint_claim(bold) == (
        "deferred: lane-B implement — sweeper owns commit"
    )
    plain = "checkpoint_claim: nothing_authored\n"
    assert extract_checkpoint_claim(plain) == "nothing_authored"
    injected = inject_checkpoint_line(
        "TYPE: CLOSEOUT\nstatus: complete\ntree_residue: 2\n",
        value="deferred: foreign WIP",
    )
    assert "checkpoint: deferred: foreign WIP" in injected


def test_extract_authored_checkpoint_from_bold_and_plain() -> None:
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        extract_authored_checkpoint,
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


def test_extract_checkpoint_claim_ignores_infra_control_line() -> None:
    """Infra ``checkpoint:`` control line is not the agent §2 claim surface."""
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        extract_checkpoint_claim,
    )

    body = """\
**checkpoint_claim:** nothing_authored

checkpoint: authored_cortex@local-master: cortex://notes/a.md deadbeef
"""
    assert extract_checkpoint_claim(body) == "nothing_authored"
    assert (
        extract_checkpoint_claim(body, allow_legacy_control_line=False)
        == "nothing_authored"
    )


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


def test_relay_table_projection_preserves_checkpoint_claim_for_gate() -> None:
    """Positive path: infra checkpoint survives §2 table projection + gate."""
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

**checkpoint_claim:** nothing_authored

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
    assert "| checkpoint_claim |" in relay_body
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


def _null_run_wrapper_without_summary_scrape(
    *,
    dispatch_id: str,
    degraded_reason: str,
    tool_call_count: int,
) -> str:
    """Realistic ImplementCloseout JSON with structured fields only (no summary scrape)."""
    return json.dumps(
        {
            "schema_version": 1,
            "status": "failed",
            "degraded_reason": degraded_reason,
            "tool_call_count": tool_call_count,
            "summary": (
                f"dispatch {dispatch_id}: {tool_call_count} tool calls, "
                "0.1s, 0B -> sidecar"
            ),
            "source_ref": f"workspaces://universal-llm-gateway/tmp/reviews/{dispatch_id}.md",
            "files_created": [],
            "files_modified": [],
            "files_deleted": [],
            "effects": [],
        }
    )


def test_null_run_zero_tool_calls_dirty_tree_reports_nothing_authored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1 — NO_RUN on a dirty tree must not credit ambient dirt as authorship."""
    dispatch_id = "auto-null-run-dirty-tree"
    _init_git_repo(tmp_path)
    _commit(tmp_path, "seed.py")
    admit_baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert admit_baseline is not None
    ambient_paths = tuple(f"ambient{i}.py" for i in range(8))
    for rel in ambient_paths:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {rel}\n", encoding="utf-8")
    ledger = CursorDispatchLedger.instance()
    monkeypatch.setattr(
        ledger,
        "read_wt_baseline",
        lambda *, dispatch_id: admit_baseline if dispatch_id else None,
    )
    from services.git_integration_worker.cursor_auto import lane_a_checkpoint

    authored = lane_a_checkpoint.authored_paths_for_dispatch(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
    )
    assert authored == ()
    wrapper = _null_run_wrapper_without_summary_scrape(
        dispatch_id=dispatch_id,
        degraded_reason="zero_tool_calls",
        tool_call_count=0,
    )
    assert degraded_reason_from_closeout_wrapper(wrapper) == "zero_tool_calls"
    assert "(degraded:" not in wrapper
    state = compute_closeout_tree_state(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        wrapper_text=wrapper,
    )
    assert state.checkpoint == "nothing_authored@local-master"
    assert state.deployment_state is None


def test_null_run_e2e_via_build_implement_closeout_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1 e2e — producer stamps structured degraded_reason; tree_state honors it."""
    dispatch_id = "auto-null-run-producer-e2e"
    _init_git_repo(tmp_path)
    _commit(tmp_path, "seed.py")
    admit_baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert admit_baseline is not None
    for i in range(8):
        rel = f"foreign{i}.py"
        (tmp_path / rel).write_text(f"# {rel}\n", encoding="utf-8")
    ledger = CursorDispatchLedger.instance()
    monkeypatch.setattr(
        ledger,
        "read_wt_baseline",
        lambda *, dispatch_id: admit_baseline if dispatch_id else None,
    )
    outcome = SdkRunOutcome(
        body="",
        status="finished",
        duration_ms=100,
        tool_call_count=0,
    )
    wrapper = build_implement_closeout_body(
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason="zero_tool_calls",
        sidecar_ref=f"workspaces://universal-llm-gateway/tmp/reviews/{dispatch_id}.md",
        result_bytes=0,
        thread_id="t-null-run",
        work_item_ref=None,
    )
    payload = json.loads(wrapper)
    assert payload["degraded_reason"] == "zero_tool_calls"
    assert payload["tool_call_count"] == 0
    state = compute_closeout_tree_state(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        wrapper_text=wrapper,
    )
    assert state.checkpoint == "nothing_authored@local-master"
    assert state.deployment_state is None


def test_null_run_empty_terminal_output_with_tool_calls_still_deferred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4 — run that authored paths still reports deferred, not nothing_authored."""
    dispatch_id = "auto-empty-output-with-tools"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    admit_baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert admit_baseline is not None
    (tmp_path / "tool_written.py").write_text("x=1\n", encoding="utf-8")
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    SeatWriteLedger.reset_instance()
    db = SeatWriteLedger(db_path=tmp_path / "seat-write-ledger.db")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.SeatWriteLedger.instance",
        lambda: db,
    )
    db.register_paths(
        arc_id=dispatch_id,
        seat_id="cursor-sdk",
        source_repo=str(tmp_path),
        paths=("tool_written.py",),
    )
    ledger = CursorDispatchLedger.instance()
    monkeypatch.setattr(
        ledger,
        "read_wt_baseline",
        lambda *, dispatch_id: admit_baseline if dispatch_id else None,
    )
    wrapper = _null_run_wrapper_without_summary_scrape(
        dispatch_id=dispatch_id,
        degraded_reason="empty_terminal_output",
        tool_call_count=3,
    )
    wrapper_obj = json.loads(wrapper)
    wrapper_obj["files_created"] = ["tool_written.py"]
    wrapper_obj["effects"] = ["tool_written.py"]
    wrapper = json.dumps(wrapper_obj)
    value = compute_lane_a_checkpoint_value(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        baseline={"admit_head": admit},
        wrapper_text=wrapper,
    )
    assert value == "deferred: authored paths not yet path-explicit committed"
    assert not null_run_suppresses_lane_a_authorship(
        degraded_reason=degraded_reason_from_closeout_wrapper(wrapper),
        wrapper_text=wrapper,
    )
    state = compute_closeout_tree_state(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        wrapper_text=wrapper,
    )
    assert "authored-not-committed" in (state.deployment_state or "")


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


def test_authored_paths_for_dispatch_intersects_seat_write_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rank 2 — delta alone never authors; ledger ∩ delta does."""
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    SeatWriteLedger.reset_instance()
    db = SeatWriteLedger(db_path=tmp_path / "seat-write-ledger.db")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.SeatWriteLedger.instance",
        lambda: db,
    )

    def _git(*args: str) -> None:
        import subprocess

        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    _git("init", "-b", "master")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "t")
    _git("commit", "--allow-empty", "-m", "seed")
    (tmp_path / "ambient.py").write_text("a=1\n", encoding="utf-8")
    from services.git_integration_worker.cursor_sdk_closeout import (
        capture_wt_baseline_with_hashes,
    )

    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    (tmp_path / "lane_edit.py").write_text("e=1\n", encoding="utf-8")
    db.register_paths(
        arc_id="arc-rank2",
        seat_id="cursor-sdk",
        source_repo=str(tmp_path),
        paths=("lane_edit.py",),
    )

    class _Ledger:
        def read_wt_baseline(self, *, dispatch_id: str) -> dict:
            return baseline

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.CursorDispatchLedger.instance",
        lambda: _Ledger(),
    )
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        authored_paths_for_dispatch,
    )

    proven = authored_paths_for_dispatch(
        source_repo=tmp_path,
        dispatch_id="auto-rank2-specimen",
    )
    assert proven == ("lane_edit.py",)
    assert "ambient.py" not in proven


def test_authored_paths_for_dispatch_signature_unchanged() -> None:
    """authored_paths_for_dispatch keeps public signature; Rank 2 adds ledger gate."""
    import inspect

    from services.git_integration_worker.cursor_auto import lane_a_checkpoint

    sig = inspect.signature(lane_a_checkpoint.authored_paths_for_dispatch)
    assert tuple(sig.parameters) == ("source_repo", "dispatch_id")
    source = inspect.getsource(lane_a_checkpoint.authored_paths_for_dispatch)
    assert "read_wt_baseline" in source
    assert "changed_paths" in source
    assert "SeatWriteLedger" in source


def _cortex_wrapper(*uris: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "status": "complete",
            "files_created": [],
            "files_modified": [],
            "files_deleted": [],
            "files_offgit_produced": list(uris),
            "effects": list(uris),
        }
    )


def _write_cortex_fixture(cortex_root: Path, uri: str, body: str) -> str:
    rel = uri.removeprefix("cortex://").lstrip("/")
    path = cortex_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_compute_checkpoint_authored_cortex_when_offgit_only(
    tmp_path: Path,
) -> None:
    """Row 19 AC1/2 — empty porcelain + cortex offgit must not stay nothing_authored.

    Population shape: auto-6f6fbce9c3df / auto-3f9411f0bd71 / a:27652 class.
    Against pre-fix code this assertion fails (returns nothing_authored).
    """
    dispatch_id = "auto-row19-cortex-only"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    cortex_root = tmp_path / "cortex-files"
    uri = "cortex://notes/system/threads/row19-fixture.md"
    digest = _write_cortex_fixture(cortex_root, uri, "durable sidecar\n")
    wrapper = _cortex_wrapper(uri)
    baseline = {"admit_head": admit}
    with patch(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.authored_paths_for_dispatch",
        return_value=(),
    ):
        value = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline=baseline,
            wrapper_text=wrapper,
            cortex_root=cortex_root,
        )
    assert value == f"authored_cortex: {uri} {digest}"
    assert value != "nothing_authored"
    body = inject_checkpoint_line("status: complete\n", value=value)
    verdict = validate_lane_a_closeout_checkpoint(
        body=body,
        require_closeout_type=False,
    )
    assert verdict.ok, verdict.reason


def test_compute_checkpoint_authored_cortex_multi_write(
    tmp_path: Path,
) -> None:
    """Row 19 AC3 — two cortex URIs → one semicolon-delimited checkpoint line."""
    dispatch_id = "auto-row19-multi"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    cortex_root = tmp_path / "cortex-files"
    uri_a = "cortex://notes/a.md"
    uri_b = "cortex://notes/b.md"
    dig_a = _write_cortex_fixture(cortex_root, uri_a, "aaa\n")
    dig_b = _write_cortex_fixture(cortex_root, uri_b, "bbb\n")
    wrapper = _cortex_wrapper(uri_a, uri_b)
    with patch(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.authored_paths_for_dispatch",
        return_value=(),
    ):
        value = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline={"admit_head": admit},
            wrapper_text=wrapper,
            cortex_root=cortex_root,
        )
    assert value == f"authored_cortex: {uri_a} {dig_a}; {uri_b} {dig_b}"
    assert validate_lane_a_closeout_checkpoint(
        body=f"checkpoint: {value}\n",
        require_closeout_type=False,
    ).ok


def test_compute_checkpoint_authored_cortex_digest_tracks_bytes(
    tmp_path: Path,
) -> None:
    """Row 19 AC4 — content change changes digest; missing file → deferred."""
    dispatch_id = "auto-row19-digest"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    cortex_root = tmp_path / "cortex-files"
    uri = "cortex://notes/digest.md"
    dig1 = _write_cortex_fixture(cortex_root, uri, "v1\n")
    with patch(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.authored_paths_for_dispatch",
        return_value=(),
    ):
        v1 = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline={"admit_head": admit},
            wrapper_text=_cortex_wrapper(uri),
            cortex_root=cortex_root,
        )
        dig2 = _write_cortex_fixture(cortex_root, uri, "v2\n")
        v2 = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline={"admit_head": admit},
            wrapper_text=_cortex_wrapper(uri),
            cortex_root=cortex_root,
        )
        missing = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline={"admit_head": admit},
            wrapper_text=_cortex_wrapper("cortex://notes/missing.md"),
            cortex_root=cortex_root,
        )
    assert v1 == f"authored_cortex: {uri} {dig1}"
    assert v2 == f"authored_cortex: {uri} {dig2}"
    assert dig1 != dig2
    assert missing == "deferred: cortex durable write could not be rehashed"
    assert rehash_cortex_uri(uri=uri, cortex_root=cortex_root) == dig2


def test_compute_checkpoint_committed_senior_to_cortex_offgit(
    tmp_path: Path,
) -> None:
    """Row 19 AC5 — lane commit wins over cortex URIs in the same wrapper."""
    dispatch_id = "auto-row19-git-senior"
    _init_git_repo(tmp_path)
    admit = _commit(tmp_path, "seed.py")
    lane_sha = _commit(tmp_path, "fix.py", dispatch_id=dispatch_id)
    cortex_root = tmp_path / "cortex-files"
    uri = "cortex://notes/also.md"
    _write_cortex_fixture(cortex_root, uri, "also\n")
    with patch(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.authored_paths_for_dispatch",
        return_value=(),
    ):
        value = compute_lane_a_checkpoint_value(
            source_repo=tmp_path,
            dispatch_id=dispatch_id,
            baseline={"admit_head": admit},
            wrapper_text=_cortex_wrapper(uri),
            cortex_root=cortex_root,
        )
    path_count = len(paths_in_commit(tmp_path, lane_sha))
    assert value == f"committed {lane_sha} paths={path_count}"
    assert not value.startswith("authored_cortex:")


def test_specimen_2_checkpoint_claim_vs_infra_authored_cortex() -> None:
    """Specimen 2 — agent §2 claim nothing_authored vs infra authored_cortex measurement."""
    from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
        annotate_checkpoint_claim_discrepancy,
        merge_plane_discrepancy_markers,
    )

    uri = "cortex://notes/system/specs/seed-fixture.md"
    digest = "a" * 64
    measurement = f"authored_cortex@local-master: {uri} {digest}"
    marker = annotate_checkpoint_claim_discrepancy(
        claim="nothing_authored",
        measurement=measurement,
    )
    assert marker == (
        f"checkpoint_claim@§2 nothing_authored@local-master "
        f"while checkpoint@infra {measurement}"
    )
    merged = merge_plane_discrepancy_markers(
        "plane-discrepancy: deployment_state@local-master lags landed@local-master",
        marker,
    )
    assert merged is not None
    assert "checkpoint_claim@§2 nothing_authored@local-master" in merged
    assert f"while checkpoint@infra {measurement}" in merged


def test_checkpoint_claim_discrepancy_silent_when_equivalent() -> None:
    from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
        annotate_checkpoint_claim_discrepancy,
    )

    assert (
        annotate_checkpoint_claim_discrepancy(
            claim="nothing_authored",
            measurement="nothing_authored@local-master",
        )
        is None
    )


def test_checkpoint_claim_discrepancy_silent_when_table_cell_carries_field_prefix() -> None:
    """7065#98 — relay table may echo ``checkpoint_claim:`` inside the Value cell."""
    from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
        annotate_checkpoint_claim_discrepancy,
    )
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        extract_checkpoint_claim,
    )

    table_body = """\
| Field | Value |
|---|---|
| checkpoint_claim | `checkpoint_claim: nothing_authored` |
"""
    claim = extract_checkpoint_claim(table_body)
    assert claim == "nothing_authored"
    assert (
        annotate_checkpoint_claim_discrepancy(
            claim=claim,
            measurement="nothing_authored@local-master",
        )
        is None
    )


def test_checkpoint_dispositions_equivalent_authored_cortex_digest_optional() -> None:
    """7065#239 — authored_cortex URI±digest does not emit defect marker."""
    from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
        checkpoint_dispositions_equivalent,
    )

    uri = "cortex://notes/system/specs/closeout-plane-discrepancy-register.md"
    digest = "d" * 64
    assert checkpoint_dispositions_equivalent(
        f"authored_cortex: {uri}",
        f"authored_cortex@local-master: {uri} {digest}",
    )


def test_checkpoint_dispositions_equivalent_committed_short_sha_and_pending() -> None:
    """7065#223 — committed short SHA and pending prose normalize before compare."""
    from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
        checkpoint_dispositions_equivalent,
    )

    full_sha = "feedfacefeedfacefeedfacefeedfacefeedface"
    short_sha = full_sha[:7]
    assert checkpoint_dispositions_equivalent(
        f"committed {short_sha} paths=1",
        f"committed@local-master {full_sha} paths=1",
    )
    assert checkpoint_dispositions_equivalent(
        f"committed {full_sha} paths=2 (+4 pending)",
        f"committed@local-master {full_sha} paths=2",
    )
