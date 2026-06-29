"""Shared sandbox scheme resolution for admission-time packet reads."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from implement_admission.closeout_helpers import cortex_files_root

_ULG_REPO_DIRNAME = "universal-llm-gateway"
_SCHEME_RE = re.compile(r"^(?P<scheme>workspaces|cortex|ws):(?://)?", re.IGNORECASE)
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
    match = _SCHEME_RE.match(raw)
    if not match:
        return ParsedSchemedPath(scheme=None, rel_path=raw)
    scheme = match.group("scheme").lower()
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


def resolve_schemed_packet(
    path_or_uri: str,
    *,
    workspaces_root_override: Path | None = None,
    cortex_root_override: Path | None = None,
) -> SchemeResolution:
    parsed = parse_schemed_path(path_or_uri)
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
