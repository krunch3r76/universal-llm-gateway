"""Record a K43 land exemption so older hub-land tests still reach merge.

Those tests cover lease, dirty-tree, and conflict behavior. The K43 gate
itself is covered by ``test_hub_land_k43_gate``. Call ``exempt_branch`` only
after the branch tip commit exists.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from services.git_integration_worker.master_review_owed import record_land_exemption


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def exempt_branch(repo: Path, branch: str) -> None:
    """Write a test exemption for ``branch`` so a pre-K43 scenario can merge.

    The ref is ``test:scenario-reaches-merge``. Callers that assert a K43
    refusal must not call this. Returns nothing; a failed record raises
    ``AssertionError`` with the status string.
    """
    tip = _git(repo, "rev-parse", "--verify", f"{branch}^{{commit}}").stdout.strip()
    base = _git(repo, "rev-parse", "--verify", f"{branch}^").stdout.strip()
    status = record_land_exemption(
        repo,
        tip,
        base_sha=base,
        exemption_ref="test:scenario-reaches-merge",
    )
    assert status == "recorded", status
