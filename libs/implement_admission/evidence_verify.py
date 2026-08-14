"""Requester-recomputed evidence predicate for closeout gates C/D.

``flatten_evidence_uris`` stays pointer-union for display. This module admits
only entries an independent reader can re-derive: artifact paths whose
published digest matches a recomputed sha256, and git refs that resolve to a
commit object. Self-minted ``bus_threads`` / ``dispatch_ids`` are excluded.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root, workspaces_root
from implement_admission.closeout_models import EvidenceUris

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_HEX_DIGEST_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")


@dataclass(frozen=True, slots=True)
class EvidenceVerifyResult:
    """Three-valued verify: admitted vs mismatch vs could-not-check."""

    admitted: list[str] = field(default_factory=list)
    mismatch: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    missing_digest: list[str] = field(default_factory=list)


def _normalize_digest(raw: str) -> str | None:
    match = _HEX_DIGEST_RE.fullmatch(raw.strip().lower())
    return match.group(1) if match else None


def _published_digest(evidence: EvidenceUris, path: str) -> str | None:
    digests = evidence.artifact_digests or {}
    if path in digests:
        return _normalize_digest(str(digests[path]))
    if "#sha256=" in path:
        return _normalize_digest(path.split("#sha256=", 1)[1])
    return None


def resolve_artifact_path(
    raw: str,
    *,
    source_repo: Path,
    cortex_root: Path,
) -> Path | None:
    """Resolve a closeout artifact path to an on-disk file, or None."""
    path = raw.split("#sha256=", 1)[0].strip()
    if not path:
        return None
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    if path.startswith("cortex://"):
        rel = path[len("cortex://") :]
        found = cortex_root / rel
        return found if found.is_file() else None
    if path.startswith("workspaces://"):
        rest = path[len("workspaces://") :]
        found = source_repo.parent / rest if source_repo.name else Path(rest)
        # workspaces://{repo}/rel — repo root is source_repo when names match
        repo_name, _, rel = rest.partition("/")
        if source_repo.name == repo_name:
            found = source_repo / rel
        else:
            found = source_repo.parent / rest
        return found if found.is_file() else None
    found = source_repo / path.lstrip("/")
    return found if found.is_file() else None


def hash_artifact_path(
    raw: str,
    *,
    source_repo: Path | None = None,
    cortex_root: Path | None = None,
) -> str | None:
    """Recompute sha256 of a resolvable artifact path. None if unreadable."""
    repo = source_repo or workspaces_root()
    cortex = cortex_root or cortex_files_root()
    resolved = resolve_artifact_path(raw, source_repo=repo, cortex_root=cortex)
    if resolved is None:
        return None
    try:
        return hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError:
        return None


def _git_commit_exists(source_repo: Path, ref: str) -> bool:
    token = ref.strip().lower()
    if token.startswith("sha256:"):
        return False
    if not _SHA_RE.fullmatch(token):
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(source_repo), "cat-file", "-e", f"{token}^{{commit}}"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def verify_evidence_uris(
    evidence: EvidenceUris,
    *,
    source_repo: Path | None = None,
    cortex_root: Path | None = None,
) -> EvidenceVerifyResult:
    """Requester-recompute artifact digests and git commit objects.

    ``cortex_assertions`` are excluded — no content-hash is available from this
    library. That is an explicit weakening: an assertion id string is not
    independently checkable here.
    """
    repo = source_repo or workspaces_root()
    cortex = cortex_root or cortex_files_root()
    admitted: list[str] = []
    mismatch: list[str] = []
    unreadable: list[str] = []
    missing_digest: list[str] = []

    for path in evidence.artifact_paths:
        published = _published_digest(evidence, path)
        if published is None:
            missing_digest.append(path)
            continue
        recomputed = hash_artifact_path(
            path, source_repo=repo, cortex_root=cortex
        )
        if recomputed is None:
            unreadable.append(path)
            continue
        if recomputed == published:
            admitted.append(path)
        else:
            mismatch.append(path)

    for ref in evidence.git_refs:
        token = ref.strip()
        if not token:
            continue
        if _git_commit_exists(repo, token):
            admitted.append(token)
        else:
            unreadable.append(token)

    return EvidenceVerifyResult(
        admitted=admitted,
        mismatch=mismatch,
        unreadable=unreadable,
        missing_digest=missing_digest,
    )


def verifiable_evidence_uris(
    evidence: EvidenceUris,
    *,
    source_repo: Path | None = None,
    cortex_root: Path | None = None,
) -> list[str]:
    """Return only evidence entries a requester independently recomputed.

    Pointer-only ``bus_threads`` / ``dispatch_ids`` never appear. Empty list
    means the gate saw nothing checkable, not that the check could not run.
    """
    return verify_evidence_uris(
        evidence, source_repo=source_repo, cortex_root=cortex_root
    ).admitted
