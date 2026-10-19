"""Shape A AC1–AC2 — conductor policy snapshot and birth seed."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.conductor_policy_snapshot import (
    default_knobs_from_route_policy,
    plan_scoreboard_birth,
    resolve_conductor_knobs,
    stamp_policy_sha256,
)
from implement_admission.conductor_score_journal import render_sparse_scoreboard


@pytest.mark.offline
def test_birth_seed() -> None:
    snap = default_knobs_from_route_policy()
    assert snap.policy_epoch == 1
    plan = plan_scoreboard_birth(
        {"rows": ["G1"]},
        hop_seq=0,
        tip_present=False,
        default_rows=("G1", "G2", "G3", "G4", "G5", "G6", "G7"),
        default_entry_gate="G1",
    )
    assert plan.rows == ("G1",)
    body = render_sparse_scoreboard(
        source_ref="todo:test-slug",
        slug="test-slug",
        entry_gate=plan.entry_gate or "G1",
        stop_after=None,
        rows=plan.rows or ("G1",),
    )
    assert "G1" in body
    again = resolve_conductor_knobs({"rows": ["G1"]}, prior=snap)
    assert again.policy_sha256 == snap.policy_sha256
    assert stamp_policy_sha256(snap.as_wire_dict()) == snap.policy_sha256


@pytest.mark.offline
def test_hop_ignores_birth_seed(tmp_path: Path) -> None:
    from implement_admission.conductor_score_journal import read_tip

    slug = "shape-a-hop-seed"
    tip_body = render_sparse_scoreboard(
        source_ref="todo:shape-a-hop-seed",
        slug=slug,
        entry_gate="G4",
        stop_after=None,
        rows=("G1", "G2", "G3", "G4"),
    )
    tip_path = tmp_path / "notes" / "system" / "scoreboards" / f"{slug}-scoreboard.md"
    tip_path.parent.mkdir(parents=True, exist_ok=True)
    tip_path.write_text(tip_body, encoding="utf-8")
    plan = plan_scoreboard_birth(
        {"rows": ["G1"], "entry_gate": "G1"},
        hop_seq=2,
        tip_present=True,
        default_rows=("G1", "G2", "G3", "G4"),
        default_entry_gate="G1",
        tip_entry_gate="G4",
    )
    assert plan.entry_gate == "G4"
    assert plan.birth_seed_ignored is True
    before = read_tip(slug, files_root=tmp_path)
    assert before is not None
    assert before[0] == tip_body


@pytest.mark.offline
def test_fold_refuses_policy_key_without_epoch_change(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    from implement_admission.conductor_score_journal import load_journal, read_tip

    slug = "ac5-fold-refuse"
    body = render_sparse_scoreboard(
        source_ref=f"todo:{slug}",
        slug=slug,
        entry_gate="G1",
        stop_after=None,
        rows=("G1",),
    )
    root = tmp_path / "notes" / "system" / "scoreboards"
    root.mkdir(parents=True)
    (root / f"{slug}-scoreboard.md").write_text(body, encoding="utf-8")
    (root / f"{slug}-score-journal.md").write_text(
        json.dumps({"policy_epoch": 1, "reason": "birth"}) + "\n",
        encoding="utf-8",
    )
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"driver": {"model": "cursor/composer-2.5"}}))
    before = read_tip(slug, files_root=tmp_path)
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "conductor-score"
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONPATH"] = str(repo / "libs")
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "fold",
            slug,
            "--payload",
            str(payload),
            "--files-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 2
    after = read_tip(slug, files_root=tmp_path)
    assert after is not None and before is not None
    assert after[1] == before[1]
    epochs = [rec.get("policy_epoch") for rec in load_journal(slug, files_root=tmp_path)]
    assert epochs == [1]


@pytest.mark.offline
def test_rebind_policy_bumps_epoch_and_sha(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    slug = "ac5-rebind"
    root = tmp_path / "notes" / "system" / "scoreboards"
    root.mkdir(parents=True)
    (root / f"{slug}-score-journal.md").write_text(
        json.dumps({"policy_epoch": 1, "reason": "birth"}) + "\n",
        encoding="utf-8",
    )
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"lane": "A"}))
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "conductor-score"
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONPATH"] = str(repo / "libs")
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "rebind-policy",
            slug,
            "--payload",
            str(payload),
            "--files-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    line = next(ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{"))
    out = json.loads(line)
    base = default_knobs_from_route_policy()
    assert out["policy_epoch"] == 2
    assert out["policy_sha256"] != base.policy_sha256
