"""Commissioner-side ``deliverables_expected`` provenance for cursor-sdk closeout.

Who calls: ``routes/cursor_sdk`` when wiring closeout delivery, and tests that
pin investigate/light-bounded packets naming durable outputs. Keeps the widen
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
from collections.abc import Iterable

from implement_admission.normalize import _files_from_packet

from services.git_integration_worker.cursor_sdk_capture_status import (
    is_allowlisted_control_plane_path,
    is_swamp_excluded_path,
)
from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    extract_instructed_paths,
)

GIT_UNREACHABLE_REASON = "git unreachable"

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
    light_bounded_expected_paths: tuple[str, ...] = (),
) -> bool:
    """Worker-set deliverables gate — implement, light-bounded paths, or packet obligation.

    Returns True when the commission requires intended-artifact evidence under G₁.
    Side effects: none (pure).
    """
    if (contract or "").lower() == "implement":
        return True
    if light_bounded_expected_paths:
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
