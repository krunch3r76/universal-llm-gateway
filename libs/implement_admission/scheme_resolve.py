"""Shared sandbox scheme resolution for admission-time packet reads."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.share_uri_registry import (
    entity_vs_file_teaching_error,
    is_cortex_entity_uri,
    leading_segment,
)

_ULG_REPO_DIRNAME = "universal-llm-gateway"
_SCHEME_RE = re.compile(
    r"^(?P<scheme>workspaces|cortex|ws|files):(?://)?", re.IGNORECASE
)
_PACKET_PREFIX = "packet:"


@dataclass(frozen=True, slots=True)
class ParsedSchemedPath:
    scheme: str | None
    rel_path: str


@dataclass(frozen=True, slots=True)
class SchemeResolution:
    parsed: ParsedSchemedPath
    sandbox_root: Path
    resolved_file: Path | None


@dataclass(frozen=True, slots=True)
class FsIngressResult:
    sandbox: str
    rel_path: str
    resolved: Path | None
    canonical_uri: str
    parsed: ParsedSchemedPath
    path_input_normalized: bool = False
    normalization_advisory: str | None = None


def workspaces_root(workspaces_root_override: Path | None = None) -> Path:
    """Return the workspaces mount root, honoring an explicit override."""
    if workspaces_root_override is not None:
        return workspaces_root_override.resolve()
    return Path(os.environ.get("PROJECT_ROOT") or "/mnt/torus/projects").resolve()


def repo_base(root: Path) -> Path:
    """Return the ULG repo directory under *root*, or *root* itself."""
    resolved = root.resolve()
    if resolved.name == _ULG_REPO_DIRNAME:
        return resolved
    nested = resolved / _ULG_REPO_DIRNAME
    return nested if nested.is_dir() else resolved


def path_contained_in(candidate: Path, root: Path) -> bool:
    """True when *candidate* resolves inside *root* (no traversal escape)."""
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def strip_admission_prefixes(path_or_uri: str) -> str:
    """Strip ``packet:`` and leading slashes from an admission path/URI."""
    raw = path_or_uri.strip()
    lower = raw.lower()
    if lower.startswith(_PACKET_PREFIX):
        raw = raw[len(_PACKET_PREFIX) :]
    return raw.lstrip("/")


def parse_schemed_path(path_or_uri: str) -> ParsedSchemedPath:
    """Parse a Share URI or bare path into scheme + relative path parts."""
    raw = strip_admission_prefixes(path_or_uri)
    if raw.lower().startswith("files://"):
        body = raw[8:]
        if body.startswith("/"):
            return ParsedSchemedPath(scheme="cortex", rel_path=body)
        return ParsedSchemedPath(scheme="cortex", rel_path=body.lstrip("/"))
    match = _SCHEME_RE.match(raw)
    if not match:
        return ParsedSchemedPath(scheme=None, rel_path=raw)
    scheme = match.group("scheme").lower()
    if scheme == "files":
        scheme = "cortex"
    rel = raw[match.end() :].lstrip("/")
    return ParsedSchemedPath(scheme=scheme, rel_path=rel)


def _reject_traversal(rel_path: str) -> bool:
    return ".." in PurePosixPath(rel_path).parts


def _packet_path_variants(packet_path: str) -> tuple[str, ...]:
    rel = packet_path.lstrip("/")
    prefix = f"{_ULG_REPO_DIRNAME}/"
    if rel.startswith(prefix):
        return (rel, rel[len(prefix) :])
    return (rel,)


def _sandbox_root(
    parsed: ParsedSchemedPath,
    *,
    workspaces_root_override: Path | None,
    cortex_root: Path | None,
) -> Path:
    if parsed.scheme == "cortex":
        return (cortex_root or cortex_files_root()).resolve()
    return workspaces_root(workspaces_root_override)


def _resolve_under_root(root: Path, rel_path: str) -> Path | None:
    root = root.resolve()
    for variant in _packet_path_variants(rel_path):
        candidate = (root / variant).resolve()
        if not path_contained_in(candidate, root):
            continue
        if candidate.is_file():
            return candidate
    return None


def path_escapes_sandbox(
    parsed: ParsedSchemedPath,
    *,
    sandbox_root: Path,
) -> bool:
    """True when *parsed* would traverse outside *sandbox_root*."""
    if _reject_traversal(parsed.rel_path):
        return True
    root = sandbox_root.resolve()
    for variant in _packet_path_variants(parsed.rel_path):
        probe = (root / variant).resolve()
        if not path_contained_in(probe, root):
            return True
    return False


def infer_sandbox_from_parsed(
    parsed: ParsedSchemedPath,
    *,
    cortex_root: Path,
    for_write: bool = False,
) -> str | None:
    """Infer ``cortex`` / ``workspaces`` sandbox from a parsed Share URI.

    Cortex entity pointers (colon form or non-existing leading segment under
    ``cortex_root``) return ``None`` so callers route to entity lookup.
    ``for_write`` engages the top-level creation gate inside
    ``is_cortex_entity_uri``.
    """
    if parsed.scheme == "cortex":
        if is_cortex_entity_uri(
            parsed.rel_path, cortex_root=cortex_root, for_write=for_write
        ):
            return None
        return "cortex"
    if parsed.scheme in ("workspaces", "ws"):
        return "workspaces"
    return None


def sandbox_scheme_conflict_message(
    explicit_sandbox: str, inferred_sandbox: str, path_or_uri: str
) -> str:
    """Build the teaching error when explicit sandbox disagrees with URI scheme."""
    return (
        f"sandbox={explicit_sandbox!r} conflicts with scheme in path={path_or_uri!r} "
        f"(infers sandbox={inferred_sandbox!r}). Pass only one disambiguator — "
        "either an explicit sandbox or a Share URI scheme "
        "(workspaces:// / cortex://), not both."
    )


def _mount_roots(
    *,
    workspaces_root_override: Path | None,
    cortex_root: Path | None,
) -> list[tuple[str, Path]]:
    ws = workspaces_root(workspaces_root_override).resolve()
    cortex = (cortex_root or cortex_files_root()).resolve()
    roots: list[tuple[str, Path]] = [("workspaces", ws), ("cortex", cortex)]
    for env_key, sandbox in (
        ("WORKSPACES_ROOT", "workspaces"),
        ("CORTEX_FILES_ROOT", "cortex"),
    ):
        raw = os.environ.get(env_key)
        if not raw:
            continue
        p = Path(raw).resolve()
        if (sandbox, p) not in roots:
            roots.append((sandbox, p))
    for alias, sandbox in (
        (Path("/mnt/torus/projects"), "workspaces"),
        (Path("/data/project"), "workspaces"),
        (Path("/data/files"), "cortex"),
        (Path("/mnt/torus/mcp-data/files"), "cortex"),
    ):
        try:
            resolved = alias.resolve()
        except OSError:
            continue
        if (sandbox, resolved) not in roots:
            roots.append((sandbox, resolved))
    return roots


def _normalize_mount_path(
    path_or_uri: str,
    *,
    workspaces_root_override: Path | None,
    cortex_root: Path | None,
) -> tuple[str, str, bool] | None:
    candidate = Path(path_or_uri.strip())
    if not candidate.is_absolute():
        return None
    resolved = candidate.resolve()
    for sandbox, root in _mount_roots(
        workspaces_root_override=workspaces_root_override,
        cortex_root=cortex_root,
    ):
        try:
            rel = resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if _reject_traversal(rel):
            return None
        return sandbox, rel, True
    return None


def _canonical_uri_for(sandbox: str, rel_path: str) -> str:
    from implement_admission.share_uri_emit import to_share_uri

    return to_share_uri(sandbox, rel_path.lstrip("/"))


def resolve_fs_ingress(
    path_or_uri: str,
    *,
    sandbox: str | None = None,
    workspaces_root_override: Path | None = None,
    cortex_root: Path | None = None,
    for_write: bool = False,
) -> FsIngressResult:
    """Resolve fs ``path`` (+ optional explicit ``sandbox``) to sandbox + rel.

    ``cortex_root`` must be supplied at in-repo call sites (tests and MCP).
    When omitted, live ``cortex_files_root()`` is used only for mount-path
    normalization of absolute host paths — never as a silent fallback for
    entity/file classification when a caller already has a root.
    ``for_write=True`` engages the top-level creation gate (absent top-level
    slash-form write raises a teaching error); nested writes under an existing
    top-level remain file paths.
    """
    raw = path_or_uri.strip()
    if not raw:
        raise ValueError("path is required")

    effective_cortex_root = (cortex_root or cortex_files_root()).resolve()
    path_input_normalized = False
    normalization_advisory: str | None = None
    mount = _normalize_mount_path(
        raw,
        workspaces_root_override=workspaces_root_override,
        cortex_root=effective_cortex_root,
    )
    if mount is not None:
        inferred_sandbox, rel_path, path_input_normalized = mount
        if path_input_normalized:
            normalization_advisory = (
                "Host mount path was accepted at ingress and normalized to Share URI "
                "form; use uri/path fields in responses, not mount absolutes."
            )
        parsed = ParsedSchemedPath(scheme=inferred_sandbox, rel_path=rel_path)
    else:
        parsed = parse_schemed_path(raw)
        if parsed.scheme == "cortex" and Path(parsed.rel_path).is_absolute():
            try:
                rel_path = (
                    Path(parsed.rel_path)
                    .resolve()
                    .relative_to(effective_cortex_root)
                    .as_posix()
                )
                parsed = ParsedSchemedPath(scheme="cortex", rel_path=rel_path)
            except ValueError:
                rel_path = parsed.rel_path.lstrip("/")
        else:
            rel_path = parsed.rel_path.lstrip("/")
        try:
            inferred = infer_sandbox_from_parsed(
                parsed,
                cortex_root=effective_cortex_root,
                for_write=for_write,
            )
        except ValueError:
            # for_write top-level creation gate — re-raise as-is
            raise
        if inferred is None and parsed.scheme == "cortex":
            raise entity_vs_file_teaching_error(raw, leading_segment(rel_path))
        if inferred is not None:
            inferred_sandbox = inferred
        elif sandbox:
            inferred_sandbox = sandbox
        else:
            raise ValueError(
                "sandbox is required when path has no Share URI scheme. "
                "Use workspaces://{repo}/{rel} or cortex://{rel}, "
                "or pass sandbox=cortex|workspaces explicitly."
            )

    if sandbox and inferred_sandbox and sandbox != inferred_sandbox:
        raise ValueError(
            sandbox_scheme_conflict_message(sandbox, inferred_sandbox, raw)
        )

    effective_sandbox = sandbox or inferred_sandbox
    if _reject_traversal(rel_path):
        raise ValueError(f"Path {raw!r} contains traversal; rejected")

    root = _sandbox_root(
        ParsedSchemedPath(
            scheme="cortex" if effective_sandbox == "cortex" else "workspaces",
            rel_path=rel_path,
        ),
        workspaces_root_override=workspaces_root_override,
        cortex_root=effective_cortex_root,
    )
    resolved = _resolve_under_root(root, rel_path)
    if resolved is None and effective_sandbox == "workspaces":
        alt_root = repo_base(root)
        if alt_root != root:
            resolved = _resolve_under_root(alt_root, rel_path)
            if resolved is not None:
                try:
                    rel_path = resolved.relative_to(alt_root.resolve()).as_posix()
                    if alt_root.name != root.name:
                        rel_path = f"{alt_root.name}/{rel_path}"
                except ValueError:
                    pass

    canonical_uri = _canonical_uri_for(effective_sandbox, rel_path)
    return FsIngressResult(
        sandbox=effective_sandbox,
        rel_path=rel_path.lstrip("/"),
        resolved=resolved,
        canonical_uri=canonical_uri,
        parsed=parsed,
        path_input_normalized=path_input_normalized,
        normalization_advisory=normalization_advisory,
    )


def _bare_path_implies_cortex(
    rel_path: str,
    *,
    cortex_root: Path,
) -> bool:
    """True when a schemeless path resolves to an existing file under cortex.

    Bare ``notes/system/specs/...`` that *exists* under CORTEX_FILES_ROOT must
    route to cortex (friction 23230). Leading-segment-only existence is too
    aggressive: live cortex may also have top-levels like ``tasks/`` that
    collide with workspaces-relative bare paths (``tasks/specs/...``).
    """
    first = leading_segment(rel_path)
    if not first or ":" in first:
        return False
    root = cortex_root.resolve()
    candidate = (root / rel_path.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return candidate.is_file()


def resolve_schemed_packet(
    path_or_uri: str,
    *,
    workspaces_root_override: Path | None = None,
    cortex_root: Path | None = None,
) -> SchemeResolution:
    """Resolve a schemed or bare packet path to sandbox root + optional file.

    Bare paths that exist as files under ``cortex_root`` are coerced to the
    cortex scheme (friction 23230). Pass ``cortex_root`` explicitly in tests
    so the live mount is never consulted.
    """
    effective_cortex_root = (cortex_root or cortex_files_root()).resolve()
    parsed = parse_schemed_path(path_or_uri)
    if parsed.scheme is None and _bare_path_implies_cortex(
        parsed.rel_path, cortex_root=effective_cortex_root
    ):
        parsed = ParsedSchemedPath(scheme="cortex", rel_path=parsed.rel_path)
    sandbox_root = _sandbox_root(
        parsed,
        workspaces_root_override=workspaces_root_override,
        cortex_root=effective_cortex_root,
    )
    if _reject_traversal(parsed.rel_path):
        return SchemeResolution(
            parsed=parsed, sandbox_root=sandbox_root, resolved_file=None
        )

    resolved = _resolve_under_root(sandbox_root, parsed.rel_path)
    if resolved is None and parsed.scheme in (None, "workspaces", "ws"):
        alt_root = repo_base(sandbox_root)
        if alt_root != sandbox_root:
            resolved = _resolve_under_root(alt_root, parsed.rel_path)
            if resolved is not None:
                sandbox_root = alt_root

    return SchemeResolution(
        parsed=parsed,
        sandbox_root=sandbox_root,
        resolved_file=resolved,
    )


def resolve_schemed_packet_file(
    path_or_uri: str,
    *,
    workspaces_root_override: Path | None = None,
    cortex_root: Path | None = None,
) -> Path | None:
    """Return the resolved packet file path, or ``None`` when missing."""
    return resolve_schemed_packet(
        path_or_uri,
        workspaces_root_override=workspaces_root_override,
        cortex_root=cortex_root,
    ).resolved_file


__all__ = [
    "FsIngressResult",
    "ParsedSchemedPath",
    "SchemeResolution",
    "infer_sandbox_from_parsed",
    "parse_schemed_path",
    "path_contained_in",
    "path_escapes_sandbox",
    "repo_base",
    "resolve_fs_ingress",
    "resolve_schemed_packet",
    "resolve_schemed_packet_file",
    "sandbox_scheme_conflict_message",
    "strip_admission_prefixes",
    "workspaces_root",
]
