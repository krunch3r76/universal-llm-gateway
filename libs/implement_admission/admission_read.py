"""Shared admission-time packet read with workspaces sandbox containment."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from implement_admission.source_ref import SourceRefError

_ULG_REPO_DIRNAME = "universal-llm-gateway"


@dataclass(frozen=True, slots=True)
class PacketRead:
    text: str
    packet_sha256: str
    resolved_path: str


def _workspaces_root(workspaces_root: Path | None) -> Path:
    if workspaces_root is not None:
        return workspaces_root.resolve()
    return Path(os.environ.get("PROJECT_ROOT") or "/mnt/torus/projects").resolve()


def _path_contained_in(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _packet_path_variants(packet_path: str) -> tuple[str, ...]:
    rel = packet_path.lstrip("/")
    prefix = f"{_ULG_REPO_DIRNAME}/"
    if rel.startswith(prefix):
        return (rel, rel[len(prefix) :])
    return (rel,)


def _resolve_packet_file(root: Path, packet_path: str) -> Path | None:
    """Resolve packet_path to an on-disk file under root (mirrors handoff.py)."""
    root = root.resolve()
    for variant in _packet_path_variants(packet_path):
        candidate = (root / variant).resolve()
        if not _path_contained_in(candidate, root):
            continue
        if candidate.is_file():
            return candidate
    return None


def _sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _frontmatter_region(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[3:end]


def frontmatter_value(text: str, key: str) -> str | None:
    """Return a YAML frontmatter scalar value, or None if absent."""
    region = _frontmatter_region(text)
    if region is None:
        return None
    match = re.search(rf"^{re.escape(key)}:\s*(\S+)", region, flags=re.MULTILINE)
    return match.group(1) if match else None


def replace_frontmatter_value(text: str, key: str, value: str) -> str:
    """Replace a frontmatter key's value; append the key if missing."""
    region = _frontmatter_region(text)
    if region is None:
        return text
    pattern = rf"^({re.escape(key)}):\s*\S+"
    replacement = f"{key}: {value}"
    if re.search(pattern, region, flags=re.MULTILINE):
        new_region = re.sub(pattern, replacement, region, count=1, flags=re.MULTILINE)
    else:
        new_region = f"{region.rstrip()}\n{replacement}"
    end = text.find("\n---", 3)
    return text[:3] + new_region + text[end:]


def _strip_review_attestation_block(frontmatter: str) -> str:
    """Remove the nested review_attestation YAML block from frontmatter."""
    lines = frontmatter.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("review_attestation:"):
            i += 1
            while i < len(lines) and (
                lines[i].startswith("  ") or lines[i].startswith("\t")
            ):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def compute_packet_sha256(text: str) -> str:
    """SHA256 with packet_sha256 PENDING and review_attestation elided."""
    elided = replace_frontmatter_value(text, "packet_sha256", "PENDING")
    region = _frontmatter_region(elided)
    if region is not None:
        stripped = _strip_review_attestation_block(region)
        if stripped != region:
            end = elided.find("\n---", 3)
            elided = elided[:3] + stripped + elided[end:]
    return _sha256_bytes(elided.encode("utf-8"))


def _normalize_packet_path(path_or_uri: str) -> str:
    raw = path_or_uri.strip()
    for prefix in ("packet:", "ws://", "cortex://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :]
    return raw.lstrip("/")


def read_packet(path_or_uri: str, *, workspaces_root: Path | None = None) -> PacketRead:
    """Read a six-block packet from workspaces-relative or URI form."""
    root = _workspaces_root(workspaces_root)
    rel = _normalize_packet_path(path_or_uri)

    for variant in _packet_path_variants(rel):
        probe = (root / variant).resolve()
        if not _path_contained_in(probe, root):
            raise SourceRefError(
                code="handoff_packet_invalid",
                source_ref=f"packet:{path_or_uri}",
                rule="path resolves outside workspaces sandbox",
            )

    candidate = _resolve_packet_file(root, rel)
    if candidate is None:
        raise SourceRefError(
            code="handoff_packet_missing",
            source_ref=f"packet:{path_or_uri}",
            rule="packet file not found under workspaces root",
        )

    data = candidate.read_bytes()
    text = data.decode("utf-8", errors="replace")
    return PacketRead(
        text=text,
        packet_sha256=compute_packet_sha256(text),
        resolved_path=str(candidate),
    )
