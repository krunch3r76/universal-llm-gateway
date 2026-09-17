"""Commissioner-side ``deliverables_expected`` provenance for cursor-sdk closeout.

Who calls: ``routes/cursor_sdk`` when wiring closeout delivery, and tests that
pin investigate/none packets naming durable outputs. Keeps the widen
logic out of the already-red ``cursor_sdk_capture_status`` module.

Invariant (todo:success-shaped-silence / operator bind 6929#534): the gate is
worker-set from commissioner packet shape — not producer self-report. A packet
that names ``files_expected``, ``evidence_required`` sidecar URIs, or
write-imperative deliverable paths forces ``deliverables_expected=True`` even
when ``contract`` is investigate/consult, so stripping ``stated_intent_no_write``
cannot launder a zero-artifact complete/shipped.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from implement_admission.normalize import _files_from_packet

from services.git_integration_worker.cursor_sdk_capture_status import (
    is_allowlisted_control_plane_path,
    is_swamp_excluded_path,
)
from services.git_integration_worker.cursor_sdk_residual_deliverable_capture import (
    extract_instructed_paths,
)

GIT_UNREACHABLE_REASON = "git unreachable"
_GIT_TIMEOUT_S = 10.0
HUB_MASTER_HEAD_RECOVERED = "hub_master_head_recovered:lane_meter_zero"
HUB_MASTER_HEAD_RECOVERY_NOT_FOUND = (
    "hub_master_head_recovery:dispatch_commit_not_found"
)
HUB_MASTER_HEAD_RECOVERY_EQUALS_TIP = (
    "hub_master_head_recovery:recovered_equals_lane_tip"
)
HUB_MASTER_HEAD_RECOVERY_NOT_AHEAD = (
    "hub_master_head_recovery:recovered_not_ahead_of_branch_point"
)

_EVIDENCE_REQUIRED_RE = re.compile(
    r"^[ \t]*evidence_required\s*:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_URI_TOKEN_RE = re.compile(r"(?:cortex|workspaces)://[^\s`>,]+")
_SHA_SUFFIX_RE = re.compile(
    r"\s*[·•]\s*sha256:.*$|\s+sha256:[0-9a-f]+.*$",
    re.IGNORECASE,
)


def _clean_uri_token(raw: str) -> str | None:
    token = raw.strip().strip("`\"'").rstrip(".,;:)>")
    token = _SHA_SUFFIX_RE.sub("", token).strip()
    if token.lower().startswith(("cortex://", "workspaces://")):
        return token
    return None


def extract_evidence_required_uris(prose: str) -> tuple[str, ...]:
    """Lift durable-share URIs from ``evidence_required:`` lines in packet prose.

    Distinct from ``extract_instructed_paths`` (write-imperative windows only) —
    a lone ``evidence_required: cortex://…`` line is citation-shaped and would
    otherwise leave ``deliverables_expected`` false for investigate packets.
    """
    if not prose:
        return ()
    ordered: list[str] = []
    seen: set[str] = set()
    for match in _EVIDENCE_REQUIRED_RE.finditer(prose):
        for uri_match in _URI_TOKEN_RE.finditer(match.group(1)):
            cleaned = _clean_uri_token(uri_match.group(0))
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                ordered.append(cleaned)
    return tuple(ordered)


def packet_names_deliverable_obligation(prose: str) -> bool:
    """True when commissioner prose names a durable output obligation.

    Covers ``files_expected`` (structured/backtick or imperative-window extract),
    write-imperative path windows, and ``evidence_required:`` sidecar URIs.
    An empty ``files_expected:`` label with no path token does not count.
    """
    if not prose:
        return False
    if _files_from_packet(prose):
        return True
    if extract_instructed_paths(prose):
        return True
    return bool(extract_evidence_required_uris(prose))


def compute_deliverables_expected(
    *,
    contract: str,
    instruction_text: str,
    residual_expected_paths: tuple[str, ...] = (),
) -> bool:
    """Worker-set deliverables gate — implement, none paths, or packet obligation.

    Returns True when the commission requires intended-artifact evidence under G₁.
    Side effects: none (pure).
    """
    if (contract or "").lower() == "implement":
        return True
    if residual_expected_paths:
        return True
    return packet_names_deliverable_obligation(instruction_text or "")


def admit_landed_true(
    *,
    ancestry_on_master: bool | None,
    commits_ahead: int | None,
) -> bool | None:
    """G₂ — project structured ``landed`` to {True, False, None}.

    Ancestry-on-master alone is vacuous when ``head_sha == branch_point``
    (measured ``commits_ahead=0``): the SHA is already on master without this
    dispatch advancing anything — emit ``False``, not ``True``. Unknown
    ancestry or an unmeasured meter must stay ``None`` (preserve-no-data);
    definite ancestry ``False`` stays ``False``. Side effects: none (pure).
    """
    if ancestry_on_master is None:
        return None
    if ancestry_on_master is False:
        return False
    if commits_ahead is None:
        return None
    return commits_ahead >= 1


def _count_commits_between(
    source_repo: Path, base_sha: str, tip_sha: str
) -> int | None:
    """Return commit count ``base_sha..tip_sha``; None when git cannot measure."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "rev-list",
                "--count",
                f"{base_sha}..{tip_sha}",
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip().isdigit():
        return None
    return int(proc.stdout.strip())


def resolve_lane_b_landed_head(
    source_repo: Path,
    *,
    dispatch_id: str,
    branch_point: str,
    lane_head_sha: str | None,
    commits_ahead: int | None,
    files_outside_repo: tuple[str, ...],
) -> tuple[str | None, int | None, str | None]:
    """Recover hub-master head when lane meter reads 0 but hub-path writes exist.

    Lane-B ``branch_state`` measures only the lane branch tip. A dispatch that
    commits path-explicitly on hub master leaves ``commits_ahead=0`` while the
    real dispatch commit sits on ``receipt_tree``. Re-resolve via
    ``recover_capture_head`` and measure ``branch_point..recovered`` so G₂ can
    admit landed without relaxing ``admit_landed_true``. Side effects: git read.
    """
    if commits_ahead != 0:
        return lane_head_sha, commits_ahead, None
    if not files_outside_repo:
        return lane_head_sha, commits_ahead, None
    if not lane_head_sha:
        return lane_head_sha, commits_ahead, None

    from services.git_integration_worker.cursor_auto.closeout_capture_head_recover import (
        recover_capture_head,
    )

    recovered_sha, _recovered_branch = recover_capture_head(
        source_repo, dispatch_id=dispatch_id
    )
    if not recovered_sha:
        return (
            lane_head_sha,
            commits_ahead,
            HUB_MASTER_HEAD_RECOVERY_NOT_FOUND,
        )
    if recovered_sha == lane_head_sha:
        return (
            lane_head_sha,
            commits_ahead,
            HUB_MASTER_HEAD_RECOVERY_EQUALS_TIP,
        )

    recovered_ahead = _count_commits_between(
        source_repo, branch_point, recovered_sha
    )
    if recovered_ahead is None or recovered_ahead < 1:
        return (
            lane_head_sha,
            commits_ahead,
            HUB_MASTER_HEAD_RECOVERY_NOT_AHEAD,
        )

    return recovered_sha, recovered_ahead, HUB_MASTER_HEAD_RECOVERED


def annotate_landed_resolution_disagreement(
    resolution_reason: str | None,
    *,
    landed: bool | None,
    ancestry_on_master: bool | None,
) -> str | None:
    """Append ancestry disagreement suffix when recovery and G₂ still diverge."""
    if not resolution_reason or not resolution_reason.startswith(
        "hub_master_head_recovered"
    ):
        return resolution_reason
    if landed is not False:
        return resolution_reason
    if ancestry_on_master is False:
        return f"{resolution_reason}:ancestry_disagrees"
    if ancestry_on_master is None:
        return f"{resolution_reason}:ancestry_unknown"
    return resolution_reason


def _has_tracked_paths(
    created: Iterable[str],
    modified: Iterable[str],
    deleted: Iterable[str],
) -> bool:
    return any(created) or any(modified) or any(deleted)


def _has_git_unreachable_effects(
    untracked: Iterable[str],
    offgit: Iterable[str],
) -> bool:
    if any(uri for uri in offgit if str(uri).strip()):
        return True
    for path in untracked:
        if not path or not str(path).strip():
            continue
        if is_allowlisted_control_plane_path(path):
            continue
        if is_swamp_excluded_path(path):
            continue
        return True
    return False


def git_land_plane_uncomputable(
    *,
    created: Iterable[str] = (),
    modified: Iterable[str] = (),
    deleted: Iterable[str] = (),
    untracked: Iterable[str] = (),
    offgit: Iterable[str] = (),
) -> bool:
    """True when git cannot see the deliverable — tracked empty, gitignored/off-git present.

    Swamp (``.cursor/``) and control-plane closeout receipts do not count.
    Side effects: none (pure).
    """
    if _has_tracked_paths(created, modified, deleted):
        return False
    return _has_git_unreachable_effects(untracked, offgit)


def suppress_vacuous_git_landed(
    landed: bool | None,
    *,
    uncomputable: bool,
) -> bool | None:
    """G₂ False is uninformative when git was not the land plane — emit None.

    Does not upgrade True. Side effects: none (pure).
    """
    if landed is not False:
        return landed
    if uncomputable:
        return None
    return landed
