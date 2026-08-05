"""G5 F-collision provocation cases — imported by harness_g5_f_collision.py.

Non-collected. Arc agent-bus:6792.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


def init_repo(root: Path) -> Path:
    """Create a tiny git repo with shared/a_only/b_only/sig/call fixtures."""
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
    (repo / "sig.py").write_text("def f():\n    return 1\n")
    (repo / "call.py").write_text("from sig import f\nprint(f())\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "base"], check=True
    )
    return repo


def provoke_f5() -> int:
    """F-5: B's post-admit edits must survive A's supersede revert."""
    from services.git_integration_worker.cursor_sdk_closeout import (
        capture_wt_baseline_with_hashes,
    )
    import services.git_integration_worker.cursor_sdk_revert as revert_mod
    from services.git_integration_worker.cursor_sdk_revert import (
        revert_dispatch_writes,
    )

    with tempfile.TemporaryDirectory(prefix="g5-f5-") as tmp:
        repo = init_repo(Path(tmp))
        baseline_a = capture_wt_baseline_with_hashes(repo)
        assert baseline_a is not None
        (repo / "a_only.py").write_text("a_wrote\n")
        (repo / "b_only.py").write_text("b_wrote\n")
        (repo / "shared.py").write_text("b_shared\n")
        ledger = MagicMock()
        ledger.read_wt_baseline.return_value = baseline_a
        ledger.count_active_write_leases.return_value = 2

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


def provoke_f1() -> int:
    """F-1: silent last-writer-wins on shared path with no loss signal."""
    from services.git_integration_worker.cursor_sdk_closeout import (
        capture_wt_baseline_with_hashes,
        changed_paths,
    )

    with tempfile.TemporaryDirectory(prefix="g5-f1-") as tmp:
        repo = init_repo(Path(tmp))
        base_a = capture_wt_baseline_with_hashes(repo)
        base_b = capture_wt_baseline_with_hashes(repo)
        (repo / "shared.py").write_text("A_MARKER\n")
        (repo / "shared.py").write_text("B_MARKER\n")
        cs_a, dev_a = changed_paths(repo, base_a)
        cs_b, dev_b = changed_paths(repo, base_b)
        final = (repo / "shared.py").read_text().strip()
        a_lost = final != "A_MARKER"
        reports_loss = any(
            ("loss" in d or "collision" in d) for d in (*dev_a, *dev_b)
        )
        a_claims = "shared.py" in (cs_a.modified + cs_a.created)
        b_claims = "shared.py" in (cs_b.modified + cs_b.created)
        print(
            "F-1 final=",
            final,
            "cs_a=",
            cs_a.modified,
            "cs_b=",
            cs_b.modified,
            "dev=",
            (*dev_a, *dev_b),
            "claims=",
            a_claims,
            b_claims,
        )
        if a_lost and not reports_loss:
            print("F-1_REPRODUCED")
            return 2
        print("F-1_SURVIVED")
        return 0


def provoke_f2() -> int:
    """F-2: torn multi-file intermediate is freely observable (no fence)."""
    with tempfile.TemporaryDirectory(prefix="g5-f2-") as tmp:
        repo = init_repo(Path(tmp))
        (repo / "sig.py").write_text("def f(x):\n    return x + 1\n")
        torn = (
            "f(x)" in (repo / "sig.py").read_text()
            and "f()" in (repo / "call.py").read_text()
        )
        print("F-2 torn_observable=", torn)
        if torn:
            print("F-2_REPRODUCED")
            return 2
        print("F-2_SURVIVED")
        return 0


def provoke_f3() -> int:
    """F-3: A's closeout claims B-only paths via baseline-diff attribution."""
    from services.git_integration_worker.cursor_sdk_closeout import (
        capture_wt_baseline_with_hashes,
        changed_paths,
    )

    with tempfile.TemporaryDirectory(prefix="g5-f3-") as tmp:
        repo = init_repo(Path(tmp))
        base_a = capture_wt_baseline_with_hashes(repo)
        (repo / "a_only.py").write_text("a_wrote\n")
        (repo / "b_only.py").write_text("b_wrote\n")
        cs_a, dev_a = changed_paths(repo, base_a)
        contaminated = "b_only.py" in cs_a.modified or "b_only.py" in cs_a.created
        print("F-3 cs_a=", cs_a, "dev_a=", dev_a, "contaminated=", contaminated)
        if contaminated:
            print("F-3_REPRODUCED")
            return 2
        print("F-3_SURVIVED")
        return 0


def provoke_f4() -> int:
    """F-4: path-explicit commit must not sweep peer dirties."""
    from libs.git_integrate.commit_paths import commit_paths

    async def _run() -> int:
        with tempfile.TemporaryDirectory(prefix="g5-f4-") as tmp:
            repo = init_repo(Path(tmp))
            (repo / "a_only.py").write_text("a_commit\n")
            (repo / "b_only.py").write_text("b_dirty\n")
            res = await commit_paths(str(repo), ["a_only.py"], "A commit")
            show = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "show",
                    "--name-only",
                    "--pretty=format:",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            names = [n for n in show.strip().splitlines() if n.strip()]
            b_swept = "b_only.py" in names
            with tempfile.TemporaryDirectory(prefix="g5-f4c-") as tmp2:
                repo2 = init_repo(Path(tmp2))
                (repo2 / "a_only.py").write_text("a_commit\n")
                (repo2 / "b_only.py").write_text("b_dirty\n")
                subprocess.run(
                    ["git", "-C", str(repo2), "add", "-A"], check=True
                )
                subprocess.run(
                    ["git", "-C", str(repo2), "commit", "-qm", "sweep"],
                    check=True,
                )
                show2 = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo2),
                        "show",
                        "--name-only",
                        "--pretty=format:",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                names2 = [n for n in show2.strip().splitlines() if n.strip()]
            print(
                "F-4 commit_paths names=",
                names,
                "b_swept=",
                b_swept,
                "res=",
                res,
                "add_A_names=",
                names2,
            )
            if b_swept:
                print("F-4_REPRODUCED")
                return 2
            if "b_only.py" in names2:
                print("F-4_SURVIVED_PATH_EXPLICIT; ADD_A_DEFEATS")
            else:
                print("F-4_SURVIVED")
            return 0

    return asyncio.run(_run())
