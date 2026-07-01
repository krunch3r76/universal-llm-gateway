"""Light-bounded dispatch deliverable capture — disk/cortex-existence verify.

Light-bounded dispatch packets almost always name a durable output path in
prose rather than the structured ``files_expected:`` field an implement
packet carries, so the baseline-diff capture machinery in
``cursor_sdk_capture_status`` never applies to them — admit-time git-baseline
capture is implement-only, so ``baseline`` is unconditionally ``None`` for a
light-bounded dispatch. This module is the independent signal for that case:
extract path-like tokens conservatively from the dispatch prose, then answer
completeness by checking whether each named path exists on disk (source repo)
or in the cortex sandbox post-dispatch — no git diff involved.
"""

from __future__ import annotations

import re
from pathlib import Path

from services.git_integration_worker.cursor_sdk_deliverable_truth import (
    LIGHT_BOUNDED_CONTRACT,
)

__all__ = [
    "LIGHT_BOUNDED_CONTRACT",
    "extract_named_paths",
    "light_bounded_capture_status",
]

# Prefixes conservative enough that a bare mention is almost always a real
# repo-relative or cortex-sandbox path, not incidental English — extends
# past a prefix to the rest of the path-shaped token.
_SANDBOX_PREFIXES = (
    "notes/system/",
    "tasks/",
    "docs/",
    "libs/",
    "services/",
    "config/",
    "scripts/",
    "pipelines/",
)
# Second, prefix-independent signal: any token carrying one of these durable
# extensions reads as a file path regardless of where it sits in the tree.
_DURABLE_EXTENSIONS = ("md", "json", "ya?ml", "txt", "csv", "html", "py")
_TRAILING_PUNCTUATION = ".,;:)]}`\"'"

_PREFIXED_PATH_RE = re.compile(
    r"(?:" + "|".join(re.escape(prefix) for prefix in _SANDBOX_PREFIXES) + r")[\w./-]+"
)
_EXTENSION_PATH_RE = re.compile(
    r"[\w][\w./-]*\.(?:" + "|".join(_DURABLE_EXTENSIONS) + r")\b", re.IGNORECASE
)


def _normalize_match(raw: str) -> str:
    return raw.strip().rstrip(_TRAILING_PUNCTUATION).lstrip("/")


def extract_named_paths(prose: str) -> tuple[str, ...]:
    """Conservative path-like token extraction from dispatch prose.

    Two independent, verb-agnostic signals — a known sandbox-prefix path or a
    bare token carrying a durable file extension — either is sufficient; no
    write-verb proximity is required. This reads the *request* packet naming
    an expected output, distinct from ``cursor_sdk_deliverable_truth``'s
    write-verb-proximate intent tell over the *response* body.
    """
    if not prose:
        return ()
    seen: set[str] = set()
    ordered: list[str] = []
    for pattern in (_PREFIXED_PATH_RE, _EXTENSION_PATH_RE):
        for match in pattern.finditer(prose):
            normalized = _normalize_match(match.group(0))
            if normalized and normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
    return tuple(ordered)


def _path_present(rel_path: str, *, source_repo: Path, cortex_root: Path) -> bool:
    rel = rel_path.lstrip("/")
    return (source_repo / rel).exists() or (cortex_root / rel).exists()


def light_bounded_capture_status(
    expected_paths: tuple[str, ...],
    *,
    source_repo: Path,
    cortex_root: Path,
) -> tuple[str, str | None]:
    """Disk-verify completeness for named light-bounded deliverable paths.

    Bypasses the implement-only baseline-diff machinery entirely: presence on
    disk (either sandbox) post-dispatch is the sole completeness signal, so a
    dispatch that actually wrote its named path is never false-degraded for
    lacking a git baseline it was never expected to have.
    """
    missing = [
        path
        for path in expected_paths
        if not _path_present(path, source_repo=source_repo, cortex_root=cortex_root)
    ]
    if missing:
        return "partial", f"divergence:light_bounded_path_absent:{missing[0]}"
    return "complete", None
