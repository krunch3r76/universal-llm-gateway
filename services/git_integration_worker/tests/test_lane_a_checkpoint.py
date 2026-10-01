"""Tests for lane-A tree residue derivation and authored-path probe."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_home import dispatch_git_identity

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










def test_specimen_2_checkpoint_claim_vs_infra_authored_cortex() -> None:
    """Specimen 2 — agent §2 claim nothing_authored vs infra authored_cortex measurement."""
    from services.git_integration_worker.relay.closeout_plane_probe import (
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
    from services.git_integration_worker.relay.closeout_plane_probe import (
        annotate_checkpoint_claim_discrepancy,
    )

    assert (
        annotate_checkpoint_claim_discrepancy(
            claim="nothing_authored",
            measurement="nothing_authored@local-master",
        )
        is None
    )




def test_checkpoint_dispositions_equivalent_authored_cortex_digest_optional() -> None:
    """7065#239 — authored_cortex URI±digest does not emit defect marker."""
    from services.git_integration_worker.relay.closeout_plane_probe import (
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
    from services.git_integration_worker.relay.closeout_plane_probe import (
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




