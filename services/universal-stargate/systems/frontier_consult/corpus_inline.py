"""Inline-only corpus URI resolution for frontier dispatch body assembly."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_seat.body_injection import build_injected_bodies_md
from agent_seat.inject_budget import INJECTED_BODY_BUDGET_BYTES
from implement_admission.scheme_resolve import (
    resolve_schemed_packet_file,
    workspaces_root,
)

CORPUS_BODY_BUDGET_BYTES = INJECTED_BODY_BUDGET_BYTES
_MARKER_PREFIX = "corpus-body"
_CORPUS_BLOCK_RE = re.compile(r"<corpus>(.*?)</corpus>", re.DOTALL | re.IGNORECASE)
_URI_RE = re.compile(r"(?:cortex|workspaces)://[^\s<>\)\]\"']+", re.IGNORECASE)


@dataclass
class CorpusResolution:
    block_md: str = ""
    injected: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @property
    def injected_bytes(self) -> int:
        return sum(int(item.get("bytes", 0)) for item in self.injected)

    @property
    def dropped_bytes(self) -> int:
        return sum(int(item.get("bytes", 0)) for item in self.dropped)


def _is_excluded_skill_uri(uri: str) -> bool:
    lower = uri.lower().rstrip("/")
    if lower.endswith("/skill.md"):
        return True
    if "/agent-skills/" in lower and lower.endswith(".md"):
        return True
    return False


def parse_corpus_uris(packet_text: str) -> tuple[str, ...]:
    """Extract document URIs from a packet ``<corpus>`` block."""
    if not packet_text:
        return ()
    match = _CORPUS_BLOCK_RE.search(packet_text)
    if not match:
        return ()
    block = match.group(1)
    seen: set[str] = set()
    ordered: list[str] = []
    for found in _URI_RE.finditer(block):
        uri = found.group(0).rstrip(".,;)")
        if _is_excluded_skill_uri(uri):
            continue
        if uri not in seen:
            seen.add(uri)
            ordered.append(uri)
    return tuple(ordered)


def _slug_from_uri(uri: str) -> str:
    path = uri.split("://", 1)[-1].rstrip("/")
    name = path.rsplit("/", 1)[-1]
    if name.lower().endswith(".md"):
        name = name[:-3]
    sanitized = re.sub(r"[^\w.-]+", "-", name).strip("-")
    return sanitized or "doc"


def _sha256_digest(body: str) -> str:
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _read_corpus_uri(uri: str, *, workspaces_root_override: Path) -> str | None:
    path = resolve_schemed_packet_file(
        uri, workspaces_root_override=workspaces_root_override
    )
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def resolve_corpus_bodies(
    uris: tuple[str, ...],
    *,
    budget_bytes: int = CORPUS_BODY_BUDGET_BYTES,
    workspaces_root_override: Path | None = None,
    already_present: str = "",
) -> CorpusResolution:
    root = (workspaces_root_override or workspaces_root()).resolve()
    entries: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for uri in uris:
        body = _read_corpus_uri(uri, workspaces_root_override=root)
        if body is None:
            unresolved.append(uri)
            continue
        entries.append(
            {
                "id": uri,
                "name": _slug_from_uri(uri),
                "digest": _sha256_digest(body),
                "body": body,
            }
        )

    block_md, injected, dropped = build_injected_bodies_md(
        "",
        entries,
        already_present=already_present,
        budget_bytes=budget_bytes,
        marker_prefix=_MARKER_PREFIX,
    )
    return CorpusResolution(
        block_md=block_md,
        injected=injected,
        dropped=dropped,
        unresolved=unresolved,
    )


def inline_corpus_for_packet(
    packet_text: str,
    *,
    budget_bytes: int = CORPUS_BODY_BUDGET_BYTES,
    workspaces_root_override: Path | None = None,
    already_present: str = "",
) -> CorpusResolution:
    uris = parse_corpus_uris(packet_text)
    if not uris:
        return CorpusResolution()
    return resolve_corpus_bodies(
        uris,
        budget_bytes=budget_bytes,
        workspaces_root_override=workspaces_root_override,
        already_present=already_present,
    )


def corpus_inline_gated(model: str) -> bool:
    """True when dispatch should inline corpus bodies (inline-only card, not SDK)."""
    from model_capabilities import inline_only as model_inline_only
    from model_id import ModelId

    if ModelId.parse(model).backend_type == "cursor_sdk":
        return False
    return model_inline_only(model)
