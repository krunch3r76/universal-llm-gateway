"""G5 F-collision harness — non-collected (not test_*.py).

Run explicitly:
  python services/git_integration_worker/tests/harness_g5_f_collision.py

Arc agent-bus:6792. Criteria:
  cortex://notes/system/threads/6792-g5-f-collision-falsification-frame.md

F-5 was REPRODUCED 2026-08-05 by auto-8f4dc8d2cdcf; gate HALTED per AC5.
Remaining falsifiers are stubs documenting unprovoked residuals.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"], check=True
    )
    (repo / "shared.py").write_text("base\n")
    (repo / "a_only.py").write_text("a0\n")
    (repo / "b_only.py").write_text("b0\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "base"], check=True
    )
    return repo


def provoke_f5() -> int:
    """F-5: B's post-admit edits must survive A's supersede revert.

    Returns 0 if survived (safe), 2 if reproduced (unsafe).
    """
    from services.git_integration_worker.cursor_sdk_closeout import (
        capture_wt_baseline_with_hashes,
    )
    import services.git_integration_worker.cursor_sdk_revert as revert_mod
    from services.git_integration_worker.cursor_sdk_revert import (
        revert_dispatch_writes,
    )

    with tempfile.TemporaryDirectory(prefix="g5-f5-") as tmp:
        repo = _init_repo(Path(tmp))
        baseline_a = capture_wt_baseline_with_hashes(repo)
        assert baseline_a is not None

        (repo / "a_only.py").write_text("a_wrote\n")
        (repo / "b_only.py").write_text("b_wrote\n")
        (repo / "shared.py").write_text("b_shared\n")

        ledger = MagicMock()
        ledger.read_wt_baseline.return_value = baseline_a

        class _Ledger:
            @classmethod
            def instance(cls):
                return ledger

        revert_mod.CursorDispatchLedger = _Ledger  # type: ignore[misc]
        report = revert_dispatch_writes(
            dispatch_id="auto-writer-A", source_repo=repo
        )

        b_survived = (repo / "b_only.py").read_text() == "b_wrote\n"
        shared_survived = (repo / "shared.py").read_text() == "b_shared\n"
        print("report=", report.as_dict())
        print("b_only=", (repo / "b_only.py").read_text().rstrip())
        print("shared=", (repo / "shared.py").read_text().rstrip())
        print("a_only=", (repo / "a_only.py").read_text().rstrip())

        if not b_survived or not shared_survived:
            print("F-5_REPRODUCED")
            return 2
        print("F-5_SURVIVED")
        return 0


def main() -> int:
    # Order: F-5 first; halt on reproduction (AC5).
    rc = provoke_f5()
    if rc != 0:
        print(
            "HALT: F-5 reproduced — F-1..F-4,F-6,F-7 unprovoked by design "
            "(see cortex://notes/system/threads/6792-g5-f-collision-gate-report.md)"
        )
        return rc
    print("F-1..F-7: extend harness after F-5 survives")
    return 0


if __name__ == "__main__":
    sys.exit(main())
