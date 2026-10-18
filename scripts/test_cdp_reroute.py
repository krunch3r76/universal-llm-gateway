"""Hermetic tests for scripts/cdp-reroute (comment-preserving policy edits)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cdp-reroute"
SOURCE_POLICY = REPO_ROOT / "config" / "routing" / "route_policy.yaml"


def _comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.lstrip().startswith("#")]


def _run(cmd: list[str], *, policy: Path, env: dict[str, str] | None = None) -> None:
    run_env = os.environ.copy()
    run_env["CDP_REROUTE_POLICY_PATH"] = str(policy)
    if env:
        run_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *cmd],
        text=True,
        capture_output=True,
        env=run_env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_on_off_preserves_comment_lines(tmp_path: Path) -> None:
    policy = tmp_path / "route_policy.yaml"
    shutil.copy(SOURCE_POLICY, policy)
    before = policy.read_text(encoding="utf-8")
    comments_before = _comment_lines(before)

    future = (datetime.now(UTC) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _run(["on", "--expires-at", future], policy=policy)
    after_on = policy.read_text(encoding="utf-8")
    assert _comment_lines(after_on) == comments_before

    data = yaml.safe_load(after_on)
    block = data["cdp_reroute"]
    assert block["enabled"] is True
    assert f"expires_at: {future}" in after_on

    _run(["off"], policy=policy)
    after_off = policy.read_text(encoding="utf-8")
    assert _comment_lines(after_off) == comments_before

    block_off = yaml.safe_load(after_off)["cdp_reroute"]
    assert block_off["enabled"] is False
    assert f"expires_at: {future}" in after_off

    assert re.search(r"^  enabled:\s*false\s*$", after_off, re.MULTILINE)
