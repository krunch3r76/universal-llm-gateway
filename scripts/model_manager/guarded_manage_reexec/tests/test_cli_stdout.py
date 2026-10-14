"""CLI stdout contract: dry-run emits a single JSON document on stdout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FAKE_SOCK = "/tmp/guarded-reexec-test-no-manage.sock"
_TARGET_REF = "0123456789abcdef0123456789abcdef01234567"


def test_dry_run_module_stdout_is_json_only() -> None:
    """Dry-run CLI stdout must parse as JSON (diagnostics on stderr only).

    Breaks when deploy_identity import-time workspace validation prints to stdout
    before the result object (friction:39210). Uses a nonexistent manage.sock so
    the test never dials live manage.
    """
    env = os.environ.copy()
    env["MANAGE_SOCKET"] = _FAKE_SOCK
    env.pop("ULG_CODE_VERSION", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.model_manager.guarded_manage_reexec",
            "--dry-run",
            "--target-ref",
            _TARGET_REF,
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert proc.returncode in (0, 2), proc.stderr
    parsed = json.loads(proc.stdout)
    assert isinstance(parsed, dict)
    assert parsed.get("status") in ("dry-run", "refused", "crashed")
