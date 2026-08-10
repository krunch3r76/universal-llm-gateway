"""Three-plane git observation for closeout assembly (a:28271).

One authority probe at assembly time — keyed by capture ``head_sha`` /
``branch`` — answers independently: commit exists (ODB)? ancestor of local
``master``? ancestor of local ``origin/master`` (no fetch)? Headline and
``@plane`` qualifiers are projections of that single observation so stranded
Lane-B work renders as a first-class state rather than a cross-field inference.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_GIT_TIMEOUT_S = 30.0
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.I)
_PLANE_LINE_RE = re.compile(r"(?im)^plane:\s*.+$")
_PLANE_DISCREPANCY_RE = re.compile(r"(?im)^plane-discrepancy:\s*.+$")


@dataclass(frozen=True, slots=True)
class CapturePlaneKeys:
    """Capture-block keys that key the three-plane probe."""

    head_sha: str | None
    branch: str | None


@dataclass(frozen=True, slots=True)
class PlaneObservation:
    """Single three-plane git observation at closeout assembly.

    ``None`` on a boolean axis means unknown — never upgraded to a definite
    weaker claim (``[universal:executor-rec]`` preserve-no-data shape).
    """

    head_sha: str | None
    branch: str | None
    commit_exists: bool | None
    landed_local_master: bool | None
    published_origin: bool | None
    unknown_reason: str | None
    as_of: str

    @property
    def is_unknown(self) -> bool:
        """True when the probe could not resolve a definite lane-B head."""
        return self.unknown_reason is not None


def parse_capture_plane_keys(wrapper_text: str | None) -> CapturePlaneKeys:
    """Extract ``head_sha`` / ``branch`` from an SDK capture JSON wrapper."""
    if not wrapper_text:
        return CapturePlaneKeys(head_sha=None, branch=None)
    raw = wrapper_text.strip()
    if not raw.startswith("{"):
        return CapturePlaneKeys(head_sha=None, branch=None)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return CapturePlaneKeys(head_sha=None, branch=None)
    if not isinstance(data, dict):
        return CapturePlaneKeys(head_sha=None, branch=None)
    head = data.get("head_sha")
    branch = data.get("branch")
    head_sha = head.strip() if isinstance(head, str) and head.strip() else None
    branch_name = (
        branch.strip() if isinstance(branch, str) and branch.strip() else None
    )
    if head_sha is not None and not _SHA_RE.match(head_sha):
        head_sha = None
    return CapturePlaneKeys(head_sha=head_sha, branch=branch_name)


def _git_ok(source_repo: Path, *args: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _commit_exists_sync(source_repo: Path, sha: str) -> bool:
    return _git_ok(
        source_repo,
        "rev-parse",
        "--quiet",
        "--verify",
        f"{sha}^{{commit}}",
    )


def _is_ancestor_of_ref(source_repo: Path, sha: str, ref: str) -> bool | None:
    """True/False when *ref* resolves; ``None`` when the tip ref is absent."""
    if not _git_ok(source_repo, "rev-parse", "--quiet", "--verify", ref):
        return None
    return _git_ok(source_repo, "merge-base", "--is-ancestor", sha, ref)


def _as_of_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def probe_three_planes(
    source_repo: Path,
    *,
    head_sha: str | None,
    branch: str | None = None,
    as_of: str | None = None,
) -> PlaneObservation:
    """Probe commit / local-master / origin planes for *head_sha* (local refs only).

    Missing capture head → ``unknown@lane-B (capture head absent)`` — never a
    positive plane. Local refs only; no fetch.
    """
    stamp = as_of or _as_of_stamp()
    if not head_sha:
        return PlaneObservation(
            head_sha=None,
            branch=branch,
            commit_exists=None,
            landed_local_master=None,
            published_origin=None,
            unknown_reason="capture head absent",
            as_of=stamp,
        )
    if not _commit_exists_sync(source_repo, head_sha):
        return PlaneObservation(
            head_sha=head_sha,
            branch=branch,
            commit_exists=False,
            landed_local_master=None,
            published_origin=None,
            unknown_reason="commit absent from ODB",
            as_of=stamp,
        )
    landed = _is_ancestor_of_ref(source_repo, head_sha, "refs/heads/master")
    published = _is_ancestor_of_ref(source_repo, head_sha, "refs/remotes/origin/master")
    return PlaneObservation(
        head_sha=head_sha,
        branch=branch,
        commit_exists=True,
        landed_local_master=landed,
        published_origin=published,
        unknown_reason=None,
        as_of=stamp,
    )


def render_plane_headline(obs: PlaneObservation) -> str:
    """Render the always-present ``plane:`` headline so stranding is grep-visible alone."""
    if obs.is_unknown:
        reason = obs.unknown_reason or "unresolved"
        return f"plane: unknown@lane-B ({reason})"
    branch_token = f"({obs.branch})" if obs.branch else ""
    parts: list[str] = []
    if obs.landed_local_master is True:
        parts.append("landed@local-master")
    elif obs.commit_exists is True:
        parts.append(f"committed@lane-B{branch_token}")
        parts.append("NOT landed@local-master")
    else:
        parts.append(f"unknown@lane-B{branch_token}")
    if obs.published_origin is True:
        parts.append("published@origin")
    elif obs.published_origin is False:
        parts.append("NOT published@origin")
    elif obs.published_origin is None and obs.landed_local_master is not None:
        # origin tip absent locally — preserve unknown, do not claim unpublished
        parts.append("unknown@origin (origin/master ref absent)")
    parts.append(f"as-of {obs.as_of}")
    return "plane: " + " · ".join(parts)


def qualify_checkpoint_value(checkpoint: str) -> str:
    """Add ``@local-master`` to unlabeled Lane-A checkpoint disposition tokens only."""
    text = checkpoint.strip()
    lead = text.split()[0] if text.split() else text
    lead_token = lead.split(":", 1)[0]
    if "@" in lead_token:
        return text
    if text.startswith("committed "):
        return "committed@local-master " + text[len("committed ") :]
    if text.startswith("deferred:"):
        return "deferred@local-master:" + text[len("deferred:") :]
    if text == "nothing_authored":
        return "nothing_authored@local-master"
    if text.startswith("authored_cortex:"):
        return "authored_cortex@local-master:" + text[len("authored_cortex:") :]
    return text


def qualify_deployment_state(deployment_state: str | None) -> str | None:
    """Add ``@local-master`` to unlabeled deployment_state obligation strings only."""
    if deployment_state is None:
        return None
    text = deployment_state.strip()
    if "@local-master" in text or "@lane-B" in text or "@origin" in text:
        return text
    if text.startswith("authored-not-committed"):
        return text.replace(
            "authored-not-committed",
            "authored-not-committed@local-master",
            1,
        )
    if "propagation-owed" in text and "@" not in text.split("—", 1)[0]:
        return text.replace("propagation-owed", "propagation-owed@local-master", 1)
    if "landed-not-live" in text and "@" not in text.split("—", 1)[0]:
        return text.replace("landed-not-live", "landed-not-live@local-master", 1)
    return f"{text} @local-master"


def checkpoint_claims_committed(checkpoint: str) -> bool:
    """True when checkpoint discloses a path-explicit commit; ``@plane`` infix OK."""
    lead = checkpoint.strip().split()[0] if checkpoint.strip() else ""
    return lead == "committed" or lead.startswith("committed@")


def checkpoint_dispositions_equivalent(claim: str, measurement: str) -> bool:
    """True when agent claim and infra measurement describe the same disposition."""
    from claude_bundles.lane_a_closeout_checkpoint import normalize_checkpoint_value

    claim_q = qualify_checkpoint_value(normalize_checkpoint_value(claim))
    measure_q = qualify_checkpoint_value(normalize_checkpoint_value(measurement))
    return claim_q == measure_q


def annotate_checkpoint_claim_discrepancy(
    *,
    claim: str | None,
    measurement: str,
) -> str | None:
    """Emit annotate-only marker when §2 claim diverges from infra ``checkpoint:``."""
    if claim is None or not claim.strip():
        return None
    if checkpoint_dispositions_equivalent(claim, measurement):
        return None
    from claude_bundles.lane_a_closeout_checkpoint import normalize_checkpoint_value

    claim_display = qualify_checkpoint_value(normalize_checkpoint_value(claim.strip()))
    measure_display = measurement.strip()
    return (
        f"checkpoint_claim@§2 {claim_display} "
        f"while checkpoint@infra {measure_display}"
    )


def merge_plane_discrepancy_markers(*parts: str | None) -> str | None:
    """Join multiple discrepancy fragments into one ``plane-discrepancy:`` line."""
    markers: list[str] = []
    for part in parts:
        if not part:
            continue
        text = part.strip()
        if text.casefold().startswith("plane-discrepancy:"):
            text = text.split(":", 1)[1].strip()
        if text:
            markers.append(text)
    if not markers:
        return None
    return "plane-discrepancy: " + "; ".join(markers)


def annotate_plane_discrepancy(
    *,
    checkpoint: str,
    deployment_state: str | None,
    plane: PlaneObservation,
) -> str | None:
    """Inline annotate-only marker when labeled fields disagree across planes.

    Never refuses emit — discrepancy is a rendered defect signal, not a gate.
    """
    markers: list[str] = []
    if plane.is_unknown:
        if checkpoint_claims_committed(checkpoint):
            reason = plane.unknown_reason or "unresolved"
            lead = checkpoint.strip().split()[0] if checkpoint.strip() else "committed"
            markers.append(
                f"plane unknown@lane-B ({reason}) while checkpoint claims {lead}"
            )
    else:
        if (
            plane.landed_local_master is True
            and deployment_state
            and "authored-not-committed" in deployment_state
        ):
            markers.append(
                "deployment_state@local-master lags landed@local-master"
            )
        if (
            plane.landed_local_master is False
            and plane.commit_exists is True
            and checkpoint_claims_committed(checkpoint)
            and "@local-master" in checkpoint.split()[0]
        ):
            markers.append(
                "checkpoint@local-master claims commit while plane NOT landed@local-master"
            )
    if not markers:
        return None
    return "plane-discrepancy: " + "; ".join(markers)


def inject_plane_line(body: str, *, value: str) -> str:
    """Replace or insert the always-present ``plane:`` line after checkpoint or residue."""
    line = value if value.startswith("plane:") else f"plane: {value}"
    if _PLANE_LINE_RE.search(body):
        return _PLANE_LINE_RE.sub(line, body, count=1)
    # Prefer after checkpoint so readers see status → residue → checkpoint → plane
    checkpoint_match = re.search(r"(?im)^checkpoint:\s*.+$", body)
    if checkpoint_match:
        insert_at = checkpoint_match.end()
        return f"{body[:insert_at]}\n{line}{body[insert_at:]}"
    residue_match = re.search(r"(?im)^tree_residue:\s*\d+\b.*$", body)
    if residue_match:
        insert_at = residue_match.end()
        return f"{body[:insert_at]}\n{line}{body[insert_at:]}"
    status_match = re.search(r"(?im)^status:\s*\S+\s*$", body)
    if status_match is None:
        return body.rstrip() + f"\n{line}\n"
    insert_at = status_match.end()
    return f"{body[:insert_at]}\n{line}{body[insert_at:]}"


def inject_plane_discrepancy_line(body: str, *, value: str | None) -> str:
    """Inject an annotate-only ``plane-discrepancy:`` marker; no-op when value is None."""
    if not value:
        return body
    line = value if value.startswith("plane-discrepancy:") else f"plane-discrepancy: {value}"
    if _PLANE_DISCREPANCY_RE.search(body):
        return _PLANE_DISCREPANCY_RE.sub(line, body, count=1)
    plane_match = _PLANE_LINE_RE.search(body)
    if plane_match:
        insert_at = plane_match.end()
        return f"{body[:insert_at]}\n{line}{body[insert_at:]}"
    return body.rstrip() + f"\n{line}\n"


def strip_plane_line(body: str) -> str:
    """Remove ``plane:`` and ``plane-discrepancy:`` lines for tests or clean re-inject."""
    lines = [
        line
        for line in body.splitlines()
        if not _PLANE_LINE_RE.match(line) and not _PLANE_DISCREPANCY_RE.match(line)
    ]
    return "\n".join(lines).rstrip() + ("\n" if body.endswith("\n") else "")


def preserve_plane_lines(body: str) -> bool:
    """True when body still carries a ``plane:`` line (relay must not drop it)."""
    return bool(_PLANE_LINE_RE.search(body or ""))


__all__ = [
    "CapturePlaneKeys",
    "PlaneObservation",
    "annotate_checkpoint_claim_discrepancy",
    "annotate_plane_discrepancy",
    "checkpoint_claims_committed",
    "checkpoint_dispositions_equivalent",
    "merge_plane_discrepancy_markers",
    "inject_plane_discrepancy_line",
    "inject_plane_line",
    "parse_capture_plane_keys",
    "preserve_plane_lines",
    "probe_three_planes",
    "qualify_checkpoint_value",
    "qualify_deployment_state",
    "render_plane_headline",
    "strip_plane_line",
]
