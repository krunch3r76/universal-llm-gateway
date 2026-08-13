"""Three-plane closeout probe — stranded / FF / unknown / annotate / relay."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
    PlaneObservation,
    annotate_checkpoint_claim_discrepancy,
    annotate_plane_discrepancy,
    apply_landed_admit_gate,
    checkpoint_dispositions_equivalent,
    inject_plane_line,
    merge_plane_discrepancy_markers,
    parse_capture_plane_keys,
    preserve_plane_lines,
    probe_three_planes,
    qualify_checkpoint_value,
    qualify_deployment_state,
    render_plane_headline,
    strip_plane_line,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    strip_projected_closeout_envelope,
)
from services.git_integration_worker.cursor_auto.closeout_tree_state import (
    compute_closeout_tree_state,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "base")
    return repo


def _wrapper(
    *,
    head_sha: str | None,
    branch: str | None,
    commits_ahead: int | None = None,
    landed: bool | None = None,
    files_untracked_or_ignored: list[str] | None = None,
    files_offgit_produced: list[str] | None = None,
) -> str:
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "complete",
        "capture_status": "complete",
        "files_created": [],
    }
    if head_sha is not None:
        payload["head_sha"] = head_sha
    if branch is not None:
        payload["branch"] = branch
    if commits_ahead is not None:
        payload["commits_ahead"] = commits_ahead
    if landed is not None:
        payload["landed"] = landed
    if files_untracked_or_ignored is not None:
        payload["files_untracked_or_ignored"] = files_untracked_or_ignored
    if files_offgit_produced is not None:
        payload["files_offgit_produced"] = files_offgit_produced
    return json.dumps(payload)


def test_parse_capture_plane_keys_from_wrapper() -> None:
    keys = parse_capture_plane_keys(
        _wrapper(head_sha="abc1234", branch="cursor-sdk/auto-3137b70eeaba")
    )
    assert keys.head_sha == "abc1234"
    assert keys.branch == "cursor-sdk/auto-3137b70eeaba"
    assert keys.commits_ahead is None
    assert keys.commits_ahead_presence == "absent"


def test_parse_capture_plane_keys_unparsed_commits_ahead() -> None:
    payload = {
        "schema_version": 1,
        "status": "complete",
        "head_sha": "abc1234",
        "commits_ahead": "not-a-number",
    }
    keys = parse_capture_plane_keys(json.dumps(payload))
    assert keys.commits_ahead is None
    assert keys.commits_ahead_presence == "unparsed"


def test_parse_capture_plane_keys_extracts_commits_ahead() -> None:
    keys = parse_capture_plane_keys(
        _wrapper(head_sha="abc1234", branch="cursor-sdk/x", commits_ahead=0)
    )
    assert keys.commits_ahead == 0
    assert keys.commits_ahead_presence == "present"
    assert keys.git_land_plane_uncomputable is False
    keys_one = parse_capture_plane_keys(
        _wrapper(head_sha="abc1234", branch="cursor-sdk/x", commits_ahead=1)
    )
    assert keys_one.commits_ahead == 1
    assert keys_one.commits_ahead_presence == "present"


def test_stranded_fixture_headline_grep_visible_not_landed(tmp_path: Path) -> None:
    """auto-3137b70eeaba shape — Lane-B commit not ancestor of master."""
    repo = _init_repo(tmp_path)
    branch = "cursor-sdk/auto-3137b70eeaba"
    _git(repo, "checkout", "-b", branch)
    (repo / "stranded.txt").write_text("stranded\n", encoding="utf-8")
    _git(repo, "add", "stranded.txt")
    _git(repo, "commit", "-m", "lane-b stranded")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    # master lacks the commit
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", head, "master"],
            capture_output=True,
        ).returncode
        != 0
    )
    obs = probe_three_planes(repo, head_sha=head, branch=branch, as_of="2026-08-07T00:00:00Z")
    line = render_plane_headline(obs)
    assert "NOT landed@local-master" in line
    assert "tip@lane-B" in line
    assert branch in line
    assert "as-of 2026-08-07T00:00:00Z" in line
    # AC2: grep-visible without joining a second field
    assert "NOT landed@local-master" in line
    assert line.startswith(f"plane: {head[:7]} · ")
    # F3 amend bind: ODB/tip rung is tip@lane-B; SHA referent is a separate · token
    assert "tip@lane-B" in line
    assert "committed@lane-B" not in line


def test_ff_landed_fixture_headline_landed_not_published(tmp_path: Path) -> None:
    """auto-d22534784ea9 shape — tip on master, origin tip absent or behind."""
    repo = _init_repo(tmp_path)
    branch = "cursor-sdk/auto-d22534784ea9"
    _git(repo, "checkout", "-b", branch)
    (repo / "landed.txt").write_text("landed\n", encoding="utf-8")
    _git(repo, "add", "landed.txt")
    _git(repo, "commit", "-m", "lane-b then ff")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    _git(repo, "merge", "--ff-only", branch)
    # no origin/master ref → unknown@origin, not a false unpublished claim...
    # AC5 wants NOT published@origin when origin is behind. Create origin behind.
    _git(repo, "update-ref", "refs/remotes/origin/master", _git(repo, "rev-parse", "HEAD~1"))
    obs = probe_three_planes(repo, head_sha=head, branch=branch, as_of="2026-08-07T00:00:00Z")
    line = render_plane_headline(obs)
    assert "landed@local-master" in line
    assert "NOT landed@local-master" not in line
    assert "NOT published@origin" in line
    assert "published@origin" not in line.split("NOT published@origin")[0]
    # Shared-referent shape: SHA at head even when no lane-B rung is shown
    assert line.startswith(f"plane: {head[:7]} · ")
    assert "tip@lane-B" not in line


def test_degraded_capture_head_absent_never_upgraded(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    obs = probe_three_planes(repo, head_sha=None, branch="cursor-sdk/x")
    line = render_plane_headline(obs)
    assert line == "plane: unknown@lane-B (capture head absent)"
    assert "landed@local-master" not in line
    assert "tip@lane-B" not in line


def test_degraded_commit_absent_from_odb(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    phantom = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    obs = probe_three_planes(repo, head_sha=phantom)
    line = render_plane_headline(obs)
    assert "unknown@lane-B" in line
    assert "commit absent from ODB" in line
    assert "landed@local-master" not in line
    assert line == f"plane: {phantom[:7]} · unknown@lane-B (commit absent from ODB)"


def test_plane_headline_sha_referent_shared_additive() -> None:
    """One short SHA names the shared capture tip; ODB rung is tip@lane-B (F3 amend bind).

    AC1 bind: commit_exists / landed / published all key off PlaneObservation.head_sha
    (probe_three_planes). Per-rung SHA rejected — would imply distinct objects.
    """
    obs = PlaneObservation(
        head_sha="2b01f241abcdef0123456789abcdef0123456789",
        branch="cursor-sdk/auto-example",
        commit_exists=True,
        landed_local_master=False,
        published_origin=False,
        unknown_reason=None,
        as_of="2026-08-12T00:00:00Z",
    )
    line = render_plane_headline(obs)
    assert line == (
        "plane: 2b01f24 · tip@lane-B(cursor-sdk/auto-example) · "
        "NOT landed@local-master · NOT published@origin · as-of 2026-08-12T00:00:00Z"
    )
    # F3 amend: tip@lane-B rung; SHA is a separate · slot
    assert "tip@lane-B(cursor-sdk/auto-example)" in line
    assert "NOT landed@local-master" in line
    assert "NOT published@origin" in line


def test_discrepancy_annotates_unknown_plane_with_committed_checkpoint(
    tmp_path: Path,
) -> None:
    """unknown@lane-B + committed checkpoint must not ship silently (bus 7068#13)."""
    repo = _init_repo(tmp_path)
    obs = probe_three_planes(repo, head_sha=None, branch="cursor-sdk/x")
    assert render_plane_headline(obs) == "plane: unknown@lane-B (capture head absent)"
    marker = annotate_plane_discrepancy(
        checkpoint="committed@local-master deadbeef paths=1",
        deployment_state=None,
        plane=obs,
    )
    assert marker is not None
    assert marker.startswith("plane-discrepancy:")
    assert "unknown" in marker.lower()
    assert "committed" in marker.lower()


def test_discrepancy_silent_when_unknown_and_checkpoint_not_committed(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    obs = probe_three_planes(repo, head_sha=None)
    marker = annotate_plane_discrepancy(
        checkpoint="nothing_authored@local-master",
        deployment_state=None,
        plane=obs,
    )
    assert marker is None


def test_checkpoint_dispositions_equivalent_authored_cortex_digest_only_delta() -> None:
    """B specimen — digest-only delta on identical authored_cortex URI is silent."""
    uri = "cortex://notes/system/threads/7065-fixture.md"
    digest = "b" * 64
    claim = f"authored_cortex: {uri}"
    measurement = f"authored_cortex@local-master: {uri} {digest}"
    assert checkpoint_dispositions_equivalent(claim, measurement)
    assert (
        annotate_checkpoint_claim_discrepancy(
            claim=claim,
            measurement=measurement,
        )
        is None
    )
    assert merge_plane_discrepancy_markers(
        annotate_checkpoint_claim_discrepancy(
            claim=claim,
            measurement=measurement,
        )
    ) is None


def test_checkpoint_dispositions_equivalent_committed_short_sha() -> None:
    """B specimen — short vs full SHA on the same object is silent."""
    full_sha = "cafebabecafebabecafebabecafebabecafebabe"
    short_sha = full_sha[:7]
    assert checkpoint_dispositions_equivalent(
        f"committed {short_sha} paths=1",
        f"committed@local-master {full_sha} paths=1",
    )


def test_discrepancy_annotates_when_deployment_lags_landed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    obs = probe_three_planes(repo, head_sha=head, as_of="t0")
    marker = annotate_plane_discrepancy(
        checkpoint="deferred@local-master: authored paths not yet path-explicit committed",
        deployment_state="authored-not-committed@local-master — 2 paths await path-explicit commit",
        plane=obs,
    )
    assert marker is not None
    assert marker.startswith("plane-discrepancy:")
    assert "lags landed@local-master" in marker


def test_specimen_auto_4696451b5b89_deployment_lags_landed_caught() -> None:
    """Falsifier specimen (7065#71) — push-only dispatch with three self-disagreements.

    Contradiction 1 (deployment authored-not-committed vs plane landed) is expressible
    and must fire ``annotate_plane_discrepancy`` at closeout_plane_probe.py:248-255.
    Contradictions 2–3 (plane vs structured_closeout_full.landed; §2 complete vs
    structured partial) are not detector inputs — fixture documents the gap.
    """
    fixture_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "closeout_plane_specimen_auto_4696451b5b89.json"
    )
    specimen = json.loads(fixture_path.read_text(encoding="utf-8"))
    inputs = specimen["detector_inputs"]
    plane_raw = inputs["plane"]
    plane = PlaneObservation(
        head_sha=plane_raw["head_sha"],
        branch=plane_raw["branch"],
        commit_exists=plane_raw["commit_exists"],
        landed_local_master=plane_raw["landed_local_master"],
        published_origin=plane_raw["published_origin"],
        unknown_reason=plane_raw["unknown_reason"],
        as_of=plane_raw["as_of"],
    )
    assert plane.landed_local_master is True
    assert "authored-not-committed" in inputs["deployment_state"]
    # Live envelope already injected this marker (7065#71); lock the arm.
    marker = annotate_plane_discrepancy(
        checkpoint=inputs["checkpoint"],
        deployment_state=inputs["deployment_state"],
        plane=plane,
    )
    assert marker == specimen["expected_marker"]
    assert specimen["contradictions"]["1_deployment_vs_landed"]["detector_expressible"]
    assert not specimen["contradictions"]["2_plane_vs_structured_landed"][
        "detector_expressible"
    ]
    assert not specimen["contradictions"]["3_status_complete_vs_structured_partial"][
        "detector_expressible"
    ]
    # Structured fields exist on the specimen but cannot be passed to the detector.
    structured = specimen["structured_closeout_full"]
    assert structured["landed"] is False
    assert structured["status"] == "partial"
    assert specimen["observed_envelope"]["status"] == "complete"


def test_relay_preserves_plane_line_through_envelope_strip() -> None:
    body = (
        "TYPE: CLOSEOUT\n"
        "status: complete\n"
        "\n"
        "status: complete\n"
        "checkpoint: committed@local-master abc1234 paths=1\n"
        "plane: tip@lane-B(cursor-sdk/x) · NOT landed@local-master · as-of t0\n"
        "plane-discrepancy: example\n"
    )
    stripped = strip_projected_closeout_envelope(body)
    assert preserve_plane_lines(stripped)
    assert "NOT landed@local-master" in stripped
    assert "plane-discrepancy: example" in stripped


def test_inject_plane_line_after_checkpoint() -> None:
    body = "status: complete\ncheckpoint: nothing_authored@local-master\n"
    out = inject_plane_line(body, value="plane: unknown@lane-B (capture head absent)")
    assert "checkpoint:" in out
    assert "plane: unknown@lane-B (capture head absent)" in out
    assert out.index("checkpoint:") < out.index("plane:")


def test_qualify_checkpoint_and_deployment_additive() -> None:
    assert (
        qualify_checkpoint_value("committed abc1234 paths=1")
        == "committed@local-master abc1234 paths=1"
    )
    assert qualify_checkpoint_value("deferred: reason") == "deferred@local-master: reason"
    assert (
        qualify_deployment_state("authored-not-committed — 2 paths await path-explicit commit")
        == "authored-not-committed@local-master — 2 paths await path-explicit commit"
    )


def test_compute_tree_state_stranded_end_to_end(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    branch = "cursor-sdk/auto-3137b70eeaba"
    _git(repo, "checkout", "-b", branch)
    (repo / "x.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "x.txt")
    _git(repo, "commit", "-m", "stranded")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    wrapper = _wrapper(head_sha=head, branch=branch)
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="deferred: authored paths not yet path-explicit committed",
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "authored_paths_for_dispatch",
        return_value=("x.txt",),
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="auto-3137b70eeaba",
            wrapper_text=wrapper,
        )
    assert "NOT landed@local-master" in state.plane_line
    assert state.checkpoint.startswith("deferred@local-master:")
    assert state.deployment_state is not None
    assert "@local-master" in state.deployment_state
    # no gate on complete — plane present regardless
    assert state.plane_line.startswith("plane:")


def test_compute_tree_state_missing_head_unknown(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    wrapper = _wrapper(head_sha=None, branch=None)
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="d-empty",
            wrapper_text=wrapper,
        )
    assert state.plane_line == "plane: unknown@lane-B (capture head absent)"
    assert state.checkpoint == "nothing_authored@local-master"


def test_compute_tree_state_lane_a_capture_head_resolves_plane(tmp_path: Path) -> None:
    """Lane-A specimen (auto-1a46033ab0e5): tip on master + commits_ahead=0 → NOT landed.

    Production Lane-A now populates commits_ahead (presence present). Zero commits is
    measured 0 — G₂ refuses vacuous ancestry-alone bare landed@local-master.
    """
    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    # Capture shape after PRIMARY populate: head_sha set, commits_ahead=0, no branch.
    wrapper = _wrapper(head_sha=head, branch=None, commits_ahead=0)
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="lane-a-head",
            wrapper_text=wrapper,
        )
    assert "unknown@lane-B (capture head absent)" not in state.plane_line
    assert "NOT landed@local-master" in state.plane_line


def test_compute_tree_state_lane_a_commits_ahead_one_reports_landed(
    tmp_path: Path,
) -> None:
    """Lane-A tip on master with commits_ahead>=1 still renders bare landed."""
    repo = _init_repo(tmp_path)
    (repo / "lane_a_landed.txt").write_text("landed\n", encoding="utf-8")
    _git(repo, "add", "lane_a_landed.txt")
    _git(repo, "commit", "-m", "lane-a progress")
    head = _git(repo, "rev-parse", "HEAD")
    wrapper = _wrapper(head_sha=head, branch=None, commits_ahead=1)
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="committed abc1234 paths=1",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="lane-a-genuine-land",
            wrapper_text=wrapper,
        )
    assert "landed@local-master" in state.plane_line
    assert "NOT landed@local-master" not in state.plane_line


def test_apply_landed_admit_gate_absent_renders_landed_unknown() -> None:
    """Absent commits_ahead + ancestry True → unknown (not landed, not NOT landed).

    Fails before three-valued landed: gate left ancestry True and headline
    rendered bare landed@local-master (A1 definite-from-absence).
    """
    plane = PlaneObservation(
        head_sha="abc1234",
        branch=None,
        commit_exists=True,
        landed_local_master=True,
        published_origin=None,
        unknown_reason=None,
        as_of="t0",
    )
    gated = apply_landed_admit_gate(
        plane,
        commits_ahead=None,
        commits_ahead_presence="absent",
    )
    assert gated.landed_local_master is None
    headline = render_plane_headline(gated)
    assert "unknown@local-master (commits_ahead absent)" in headline
    assert "tip@lane-B" in headline
    assert "landed@local-master" not in headline.replace(
        "unknown@local-master", ""
    ).replace("NOT landed@local-master", "")
    assert "NOT landed@local-master" not in headline


@pytest.mark.parametrize(
    ("presence", "commits_ahead", "expected_verdict", "reason_substr"),
    [
        ("absent", None, "unknown", "commits_ahead absent"),  # A1 key-omitted
        ("absent", None, "unknown", "commits_ahead absent"),  # A2 null→absent
        ("unparsed", None, "unknown", "commits_ahead unparsed"),  # U1 non-int
        ("unparsed", None, "unknown", "commits_ahead unparsed"),  # U2 negative
        ("present", 0, "NOT landed", None),  # P0
        ("present", 1, "landed", None),  # P>=1
    ],
    ids=["A1", "A2", "U1", "U2", "P0", "P_ge_1"],
)
def test_landed_axis_state_table_ancestry_true(
    presence: str,
    commits_ahead: int | None,
    expected_verdict: str,
    reason_substr: str | None,
) -> None:
    """Six-row landed-axis table (ancestry True) — every row an explicit verdict."""
    plane = PlaneObservation(
        head_sha="abc1234",
        branch="cursor-sdk/auto-axis",
        commit_exists=True,
        landed_local_master=True,
        published_origin=False,
        unknown_reason=None,
        as_of="t0",
    )
    gated = apply_landed_admit_gate(
        plane,
        commits_ahead=commits_ahead,
        commits_ahead_presence=presence,  # type: ignore[arg-type]
    )
    headline = render_plane_headline(gated)
    bare = headline.replace("NOT landed@local-master", "")
    if expected_verdict == "unknown":
        assert gated.landed_local_master is None
        assert f"unknown@local-master ({reason_substr})" in headline
        assert "tip@lane-B" in headline
        assert "landed@local-master" not in bare
        assert "NOT landed@local-master" not in headline
    elif expected_verdict == "NOT landed":
        assert gated.landed_local_master is False
        assert "NOT landed@local-master" in headline
        assert "landed@local-master" not in bare
    else:
        assert gated.landed_local_master is True
        assert "landed@local-master" in bare
        assert "NOT landed@local-master" not in headline
        assert "unknown@local-master" not in headline


def test_landed_axis_parse_a1_a2_u1_u2_feed_gate_unknown() -> None:
    """Classify paths A1/A2/U1/U2 → presence≠present → gate unknown (not bare landed)."""
    cases = [
        ({"head_sha": "abc1234"}, "absent", "commits_ahead absent"),  # A1
        ({"head_sha": "abc1234", "commits_ahead": None}, "absent", "commits_ahead absent"),  # A2
        (
            {"head_sha": "abc1234", "commits_ahead": "not-a-number"},
            "unparsed",
            "commits_ahead unparsed",
        ),  # U1
        ({"head_sha": "abc1234", "commits_ahead": -1}, "unparsed", "commits_ahead unparsed"),  # U2
    ]
    plane = PlaneObservation(
        head_sha="abc1234",
        branch=None,
        commit_exists=True,
        landed_local_master=True,
        published_origin=None,
        unknown_reason=None,
        as_of="t0",
    )
    for payload, presence, reason in cases:
        keys = parse_capture_plane_keys(json.dumps(payload))
        assert keys.commits_ahead_presence == presence
        gated = apply_landed_admit_gate(
            plane,
            commits_ahead=keys.commits_ahead,
            commits_ahead_presence=keys.commits_ahead_presence,
        )
        line = render_plane_headline(gated)
        assert gated.landed_local_master is None, payload
        assert f"unknown@local-master ({reason})" in line, payload


def test_compute_tree_state_absent_commits_ahead_renders_landed_unknown(
    tmp_path: Path,
) -> None:
    """Compose arm: tip on master + key-omitted commits_ahead → unknown@local-master."""
    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    wrapper = _wrapper(head_sha=head, branch=None)  # A1 — key omitted
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="lane-a-absent-ahead",
            wrapper_text=wrapper,
        )
    assert "unknown@local-master (commits_ahead absent)" in state.plane_line
    assert "tip@lane-B" in state.plane_line
    bare = state.plane_line.replace("unknown@local-master (commits_ahead absent)", "")
    assert "landed@local-master" not in bare
    assert "NOT landed@local-master" not in state.plane_line


def test_landed_axis_unknown_skips_not_landed_discrepancy_arm() -> None:
    """Landed unknown is not absence-of-NOT — annotate NOT-landed clash stays silent."""
    plane = PlaneObservation(
        head_sha="abc1234",
        branch=None,
        commit_exists=True,
        landed_local_master=True,
        published_origin=None,
        unknown_reason=None,
        as_of="t0",
    )
    gated = apply_landed_admit_gate(
        plane,
        commits_ahead=None,
        commits_ahead_presence="absent",
    )
    assert gated.landed_local_master is None
    marker = annotate_plane_discrepancy(
        checkpoint="committed@local-master deadbeef paths=1",
        deployment_state=None,
        plane=gated,
    )
    assert marker is None


def test_apply_landed_admit_gate_present_zero_refuses_landed() -> None:
    """Measured commits_ahead=0 must refuse vacuous landed@local-master."""
    plane = PlaneObservation(
        head_sha="abc1234",
        branch="cursor-sdk/auto-vacuous",
        commit_exists=True,
        landed_local_master=True,
        published_origin=None,
        unknown_reason=None,
        as_of="t0",
    )
    gated = apply_landed_admit_gate(
        plane,
        commits_ahead=0,
        commits_ahead_presence="present",
    )
    assert gated.landed_local_master is False
    headline = render_plane_headline(gated)
    assert "NOT landed@local-master" in headline


def test_vacuous_tip_on_master_commits_ahead_zero_not_landed(tmp_path: Path) -> None:
    """G₂ vacuous positive — tip on master with commits_ahead=0 must NOT claim landed."""
    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    wrapper = _wrapper(head_sha=head, branch="cursor-sdk/auto-vacuous", commits_ahead=0)
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="auto-vacuous",
            wrapper_text=wrapper,
        )
    assert "NOT landed@local-master" in state.plane_line
    plane_body = state.plane_line.split("plane:", 1)[1]
    assert "landed@local-master" not in plane_body.replace("NOT landed@local-master", "")


def test_vacuous_landed_false_wrapper_still_not_landed(tmp_path: Path) -> None:
    """Optional landed:false in wrapper does not override G₂ admit gate."""
    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    wrapper = _wrapper(
        head_sha=head,
        branch="cursor-sdk/auto-vacuous",
        commits_ahead=0,
        landed=False,
    )
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="auto-vacuous-false",
            wrapper_text=wrapper,
        )
    assert "NOT landed@local-master" in state.plane_line


def test_genuine_land_commits_ahead_one_reports_landed(tmp_path: Path) -> None:
    """Genuine land — branch commit merged to master with commits_ahead=1."""
    repo = _init_repo(tmp_path)
    branch = "cursor-sdk/auto-genuine-land"
    _git(repo, "checkout", "-b", branch)
    (repo / "landed.txt").write_text("landed\n", encoding="utf-8")
    _git(repo, "add", "landed.txt")
    _git(repo, "commit", "-m", "lane-b progress")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    _git(repo, "merge", "--ff-only", branch)
    wrapper = _wrapper(head_sha=head, branch=branch, commits_ahead=1)
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="committed abc1234 paths=1",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="auto-genuine-land",
            wrapper_text=wrapper,
        )
    assert "landed@local-master" in state.plane_line
    assert "NOT landed@local-master" not in state.plane_line


def test_gitignored_only_commits_ahead_zero_plane_unknown_not_not_landed(
    tmp_path: Path,
) -> None:
    """Git-unreachable-only effects: measured 0 must not project NOT landed."""
    from services.git_integration_worker.cursor_sdk_deliverables_expected import (
        GIT_UNREACHABLE_REASON,
    )

    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    wrapper = _wrapper(
        head_sha=head,
        branch="cursor-sdk/auto-gitignored",
        commits_ahead=0,
        files_untracked_or_ignored=[".claude/skills/x/SKILL.md"],
    )
    with patch(
        "services.git_integration_worker.cursor_auto.closeout_tree_state."
        "compute_lane_a_checkpoint_value",
        return_value="nothing_authored",
    ):
        state = compute_closeout_tree_state(
            source_repo=repo,
            dispatch_id="auto-gitignored",
            wrapper_text=wrapper,
        )
    assert f"unknown@local-master ({GIT_UNREACHABLE_REASON})" in state.plane_line
    assert "NOT landed@local-master" not in state.plane_line


def test_strip_plane_line_roundtrip() -> None:
    body = "status: complete\nplane: landed@local-master · as-of t0\n"
    assert "plane:" not in strip_plane_line(body)
