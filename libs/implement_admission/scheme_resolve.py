"""Shared sandbox scheme resolution for admission-time packet reads."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.share_uri_registry import (
    CORTEX_FILE_ROOT_DIRS,
    is_cortex_entity_uri,
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
    if workspaces_root_override is not None:
        return workspaces_root_override.resolve()
    return Path(os.environ.get("PROJECT_ROOT") or "/mnt/torus/projects").resolve()


def repo_base(root: Path) -> Path:
    resolved = root.resolve()
    if resolved.name == _ULG_REPO_DIRNAME:
        return resolved
    nested = resolved / _ULG_REPO_DIRNAME
    return nested if nested.is_dir() else resolved


def path_contained_in(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def strip_admission_prefixes(path_or_uri: str) -> str:
    raw = path_or_uri.strip()
    lower = raw.lower()
    if lower.startswith(_PACKET_PREFIX):
        raw = raw[len(_PACKET_PREFIX) :]
    return raw.lstrip("/")


def parse_schemed_path(path_or_uri: str) -> ParsedSchemedPath:
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
    cortex_root_override: Path | None,
) -> Path:
    if parsed.scheme == "cortex":
        return (cortex_root_override or cortex_files_root()).resolve()
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
    if _reject_traversal(parsed.rel_path):
        return True
    root = sandbox_root.resolve()
    for variant in _packet_path_variants(parsed.rel_path):
        probe = (root / variant).resolve()
        if not path_contained_in(probe, root):
            return True
    return False


def infer_sandbox_from_parsed(parsed: ParsedSchemedPath) -> str | None:
    if parsed.scheme == "cortex":
        if is_cortex_entity_uri(parsed.rel_path):
            return None
        return "cortex"
    if parsed.scheme in ("workspaces", "ws"):
        return "workspaces"
    return None


def sandbox_scheme_conflict_message(
    explicit_sandbox: str, inferred_sandbox: str, path_or_uri: str
) -> str:
    return (
        f"sandbox={explicit_sandbox!r} conflicts with scheme in path={path_or_uri!r} "
        f"(infers sandbox={inferred_sandbox!r}). Pass only one disambiguator — "
        "either an explicit sandbox or a Share URI scheme "
        "(workspaces:// / cortex://), not both."
    )


def _mount_roots(
    *,
    workspaces_root_override: Path | None,
    cortex_root_override: Path | None,
) -> list[tuple[str, Path]]:
    ws = workspaces_root(workspaces_root_override).resolve()
    cortex = (cortex_root_override or cortex_files_root()).resolve()
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
    cortex_root_override: Path | None,
) -> tuple[str, str, bool] | None:
    candidate = Path(path_or_uri.strip())
    if not candidate.is_absolute():
        return None
    resolved = candidate.resolve()
    for sandbox, root in _mount_roots(
        workspaces_root_override=workspaces_root_override,
        cortex_root_override=cortex_root_override,
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
    cortex_root_override: Path | None = None,
) -> FsIngressResult:
    """Resolve fs ``path`` (+ optional explicit ``sandbox``) to sandbox + rel."""
    raw = path_or_uri.strip()
    if not raw:
        raise ValueError("path is required")

    path_input_normalized = False
    normalization_advisory: str | None = None
    mount = _normalize_mount_path(
        raw,
        workspaces_root_override=workspaces_root_override,
        cortex_root_override=cortex_root_override,
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
            cortex_root = (cortex_root_override or cortex_files_root()).resolve()
            try:
                rel_path = (
                    Path(parsed.rel_path).resolve().relative_to(cortex_root).as_posix()
                )
                parsed = ParsedSchemedPath(scheme="cortex", rel_path=rel_path)
            except ValueError:
                rel_path = parsed.rel_path.lstrip("/")
        else:
            rel_path = parsed.rel_path.lstrip("/")
        inferred = infer_sandbox_from_parsed(parsed)
        if inferred is None and parsed.scheme == "cortex":
            raise ValueError(
                f"cortex:// path {raw!r} is not a file-root path "
                f"(known dirs: {', '.join(sorted(CORTEX_FILE_ROOT_DIRS))}); "
                "entity URIs are resolved via cortex entity lookup, not fs."
            )
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
        cortex_root_override=cortex_root_override,
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


def _bare_path_implies_cortex(rel_path: str) -> bool:
    """True when a schemeless path is under a known cortex file-root dir.

    Bare ``notes/system/specs/...`` (and other CORTEX_FILE_ROOT_DIRS prefixes)
    live under CORTEX_FILES_ROOT, not PROJECT_ROOT. Without this, admission
    readers that strip ``cortex://`` (or cite the bare form) resolve under
    workspaces and spuriously report ``implement_spec_unreadable`` while
    ``doc_validate(path=cortex://...)`` and ``fs(sandbox=cortex)`` succeed
    (friction 23230).
    """
    first = rel_path.strip("/").split("/", 1)[0].lower()
    return first in {d.lower() for d in CORTEX_FILE_ROOT_DIRS}


def resolve_schemed_packet(
    path_or_uri: str,
    *,
    workspaces_root_override: Path | None = None,
    cortex_root_override: Path | None = None,
) -> SchemeResolution:
    parsed = parse_schemed_path(path_or_uri)
    if parsed.scheme is None and _bare_path_implies_cortex(parsed.rel_path):
        parsed = ParsedSchemedPath(scheme="cortex", rel_path=parsed.rel_path)
    sandbox_root = _sandbox_root(
        parsed,
        workspaces_root_override=workspaces_root_override,
        cortex_root_override=cortex_root_override,
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
    cortex_root_override: Path | None = None,
) -> Path | None:
    return resolve_schemed_packet(
        path_or_uri,
        workspaces_root_override=workspaces_root_override,
        cortex_root_override=cortex_root_override,
    ).resolved_file


__all__ = [
    "CORTEX_FILE_ROOT_DIRS",
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
