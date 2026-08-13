"""Light-bounded dispatch deliverable capture — disk/cortex-existence verify.

Light-bounded dispatches carry ``baseline=None`` (implement-only admit snapshot),
so ``cursor_sdk_capture_status`` baseline diff never applies. This module is the
independent completeness signal: paths come **only** from the packet's
``files_expected:`` deliverable field (AC8 — prose citations and read-loci must
not enter the expected set), then disk/cortex presence post-dispatch is the sole
signal — no git diff involved.
"""

from __future__ import annotations

import re
from pathlib import Path

from implement_admission.closeout_models import EffectsManifest

from services.git_integration_worker.cursor_sdk_deliverable_truth import (
    LIGHT_BOUNDED_CONTRACT,
)

__all__ = [
    "LIGHT_BOUNDED_CONTRACT",
    "extract_instructed_paths",
    "first_landed_fs_uri",
    "fs_write_landed",
    "light_bounded_capture_status",
    "light_bounded_deliverable_present",
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
_SCHEME_PATH_RE = re.compile(r"(?:cortex|workspaces)://[\w./-]+", re.IGNORECASE)
_FILES_EXPECTED_LINE_RE = re.compile(r"(?i)^files_expected:\s*(.*)$")
_TOP_LEVEL_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*:\s", re.IGNORECASE)
_BULLET_PREFIX_RE = re.compile(r"^[-*]\s+")
_NONE_TOKENS = frozenset({"none", "n/a", "na", "null"})


def _normalize_match(raw: str) -> str:
    return raw.strip().rstrip(_TRAILING_PUNCTUATION).lstrip("/")


def _normalize_scheme_path(raw: str) -> str:
    normalized = _normalize_match(raw)
    match = re.match(r"(?i)(?:cortex|workspaces)://(.+)", normalized)
    if not match:
        return normalized
    rest = match.group(1)
    if normalized.lower().startswith("workspaces://"):
        parts = rest.split("/", 1)
        return parts[1] if len(parts) > 1 else rest
    return rest


def _path_patterns() -> tuple[re.Pattern[str], ...]:
    return (_SCHEME_PATH_RE, _PREFIXED_PATH_RE, _EXTENSION_PATH_RE)


def _extract_paths_from_line(line: str, *, leading_token_only: bool) -> list[str]:
    stripped = line.strip()
    if not stripped:
        return []
    content = _BULLET_PREFIX_RE.sub("", stripped) if leading_token_only else stripped
    if leading_token_only:
        for pattern in _path_patterns():
            match = pattern.match(content)
            if match:
                return [_normalize_scheme_path(_normalize_match(match.group(0)))]
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for pattern in _path_patterns():
        for match in pattern.finditer(content):
            normalized = _normalize_scheme_path(_normalize_match(match.group(0)))
            if normalized and normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
    return ordered


def _files_expected_field_text(prose: str) -> str:
    """Return body text under the ``files_expected:`` field only."""
    if not prose:
        return ""
    lines = prose.splitlines()
    block: list[str] = []
    in_field = False
    for line in lines:
        stripped = line.strip()
        if not in_field:
            match = _FILES_EXPECTED_LINE_RE.match(stripped)
            if not match:
                continue
            in_field = True
            inline = match.group(1).strip()
            if inline:
                block.append(inline)
            continue
        if not stripped:
            continue
        if _TOP_LEVEL_FIELD_RE.match(stripped):
            break
        if block and not _BULLET_PREFIX_RE.match(stripped):
            break
        block.append(stripped)
    return "\n".join(block)


def extract_instructed_paths(prose: str) -> tuple[str, ...]:
    """Extract deliverable paths declared in the packet ``files_expected:`` field.

    Body prose — citations, read-loci, out-of-scope paths — is ignored (AC8).
    English-only values (``cortex seed artifacts + todo mint``) yield an empty
    tuple so the incompleteness gate does not false-probe prose obligations.
    """
    field_text = _files_expected_field_text(prose)
    if not field_text:
        return ()
    lowered = field_text.strip().lower()
    if lowered in _NONE_TOKENS:
        return ()
    seen: set[str] = set()
    ordered: list[str] = []
    for line in field_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for path in _extract_paths_from_line(stripped, leading_token_only=False):
            if path and path not in seen:
                seen.add(path)
                ordered.append(path)
    return tuple(ordered)


def _path_present(rel_path: str, *, source_repo: Path, cortex_root: Path) -> bool:
    rel = rel_path.lstrip("/")
    return (source_repo / rel).exists() or (cortex_root / rel).exists()


def _resolve_fs_target_path(
    raw_target: str,
    *,
    source_repo: Path,
    cortex_root: Path,
    mount_root: Path,
) -> Path | None:
    raw = raw_target.strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower.startswith("cortex://"):
        rel = _normalize_scheme_path(raw).lstrip("/")
        return cortex_root / rel
    if lower.startswith("cortex:"):
        rel = raw.split(":", 1)[1].lstrip("/")
        return cortex_root / rel
    if lower.startswith("workspaces://"):
        rel = _normalize_scheme_path(raw).lstrip("/")
        return mount_root / rel
    if lower.startswith("workspaces:"):
        rel = raw.split(":", 1)[1].lstrip("/")
        return mount_root / rel
    if ":" in raw:
        sandbox, _, path = raw.partition(":")
        sandbox_key = sandbox.strip().lower()
        rel = path.strip().lstrip("/")
        if sandbox_key == "cortex":
            return cortex_root / rel
        if sandbox_key == "workspaces":
            return mount_root / rel
    rel = raw.lstrip("/")
    repo_path = source_repo / rel
    if repo_path.exists():
        return repo_path
    cortex_path = cortex_root / rel
    if cortex_path.exists():
        return cortex_path
    return None


def _landed_fs_write_uris(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path,
    cortex_root: Path,
) -> list[str]:
    if manifest is None:
        return []
    from services.git_integration_worker.cursor_sdk_manifest import (
        _normalize_offgit_uri,
        manifest_fs_write_targets,
        resolve_mount_root,
    )

    mount_root = resolve_mount_root(source_repo)
    uris: list[str] = []
    for sandbox, path in manifest_fs_write_targets(manifest):
        lookup = f"{sandbox}:{path}" if sandbox else path
        resolved = _resolve_fs_target_path(
            lookup,
            source_repo=source_repo,
            cortex_root=cortex_root,
            mount_root=mount_root,
        )
        if resolved is not None and resolved.exists():
            uris.append(_normalize_offgit_uri(sandbox, path))
    return uris


def fs_write_landed(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path,
    cortex_root: Path,
) -> bool:
    """True when a write-family fs manifest entry targets an existing path."""
    return bool(
        _landed_fs_write_uris(
            manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
    )


def first_landed_fs_uri(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path,
    cortex_root: Path,
) -> str:
    uris = _landed_fs_write_uris(
        manifest,
        source_repo=source_repo,
        cortex_root=cortex_root,
    )
    return uris[0] if uris else ""


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


def light_bounded_deliverable_present(
    expected_paths: tuple[str, ...],
    *,
    source_repo: Path,
    cortex_root: Path,
) -> bool:
    """True iff every declared light-bounded deliverable path is present.

    Ground-truth completeness signal for the reason-birth suppression in
    ``cursor_sdk_deliverable_truth.light_bounded_deliverable_reason``: when the
    packet-declared deliverable(s) all exist on disk (source repo) or in the
    cortex sandbox post-dispatch, a tool-call-stream ``stated_intent_no_write``
    inference is a false negative — the SDK stream simply did not surface the
    write (e.g. a cortex sidecar; cf. the 22454 ``zero_tool_calls`` gap).

    Empty ``expected_paths`` => ``False`` (no declared deliverable to verify, so
    nothing to suppress). Named paths only — never scans the working tree — so
    it cannot over-attribute background/non-agent writes (no 22316-direction
    over-capture). It does NOT prove *this run* wrote the path (no mtime /
    baseline-hash check; those remain RC-3 surfaces); presence is treated as
    completeness, consistent with ``light_bounded_capture_status``.
    """
    if not expected_paths:
        return False
    return all(
        _path_present(path, source_repo=source_repo, cortex_root=cortex_root)
        for path in expected_paths
    )
