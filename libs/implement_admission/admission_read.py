"""Shared admission-time packet read with workspaces sandbox containment."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from implement_admission.scheme_resolve import (
    parse_schemed_path,
    path_escapes_sandbox,
    resolve_schemed_packet,
)
from implement_admission.source_ref import SourceRefError


@dataclass(frozen=True, slots=True)
class PacketRead:
    text: str
    packet_sha256: str
    resolved_path: str


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


def frontmatter_list_value(text: str, key: str) -> list[str] | None:
    """Return a YAML frontmatter block-list value, or None if the key is absent.

    Only the block-list shape is supported (``key:`` alone on its line,
    followed by indented ``- item`` lines) — the shape packet authors already
    use to declare an authoritative ``files_expected:`` scope (friction
    a:31774). A present-but-empty block (``key:`` with no following ``-``
    lines) returns ``[]``, distinct from an absent key (``None``), so an
    author can declare an explicit empty scope.
    """
    region = _frontmatter_region(text)
    if region is None:
        return None
    header = re.search(rf"^{re.escape(key)}:[ \t]*$", region, flags=re.MULTILINE)
    if header is None:
        return None
    items: list[str] = []
    for line in region[header.end() :].splitlines():
        if not line.strip():
            continue
        item = re.match(r"^[ \t]+-[ \t]*(.+)$", line)
        if item is None:
            break
        items.append(item.group(1).strip())
    return items


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


def _sandbox_label(parsed_scheme: str | None) -> str:
    if parsed_scheme == "cortex":
        return "cortex sandbox"
    return "workspaces sandbox"


def read_packet(path_or_uri: str, *, workspaces_root: Path | None = None) -> PacketRead:
    """Read a six-block packet from workspaces- or cortex-schemed URI form."""
    parsed = parse_schemed_path(path_or_uri)
    resolution = resolve_schemed_packet(
        path_or_uri,
        workspaces_root_override=workspaces_root,
    )

    if path_escapes_sandbox(parsed, sandbox_root=resolution.sandbox_root):
        raise SourceRefError(
            code="handoff_packet_invalid",
            source_ref=f"packet:{path_or_uri}",
            rule=f"path resolves outside {_sandbox_label(parsed.scheme)}",
        )

    if resolution.resolved_file is None:
        root = resolution.sandbox_root
        raise SourceRefError(
            code="handoff_packet_missing",
            source_ref=f"packet:{path_or_uri}",
            rule=(
                f"packet file not found under {_sandbox_label(parsed.scheme)} "
                f"root ({root}). "
                "Cortex packet paths must resolve under CORTEX_FILES_ROOT "
                "(MCP container: /data/files; host default: ~/mcp-data/files). "
                "Allowed cortex file-root prefixes include notes/, ephemeral/, "
                "dropbox/, uploads/, exports/, trash/, agent-skills/."
            ),
        )

    data = resolution.resolved_file.read_bytes()
    text = data.decode("utf-8", errors="replace")
    return PacketRead(
        text=text,
        packet_sha256=compute_packet_sha256(text),
        resolved_path=str(resolution.resolved_file),
    )
