"""Life-surface (/mcp/life) packet + corpus mirror for web-anthropic handoffs.

Option 2 (`todo:life-fs-workspaces-unbound`): life refuses workspaces reads.
Web handoffs must land a cortex mirror and point the bus turn at it.
Friction a23964; live NEED agent-bus:4986.

Mirror target (`todo:life-handoff-ephemeral-prefix`): dedicated ephemeral
prefix ``ephemeral/handoffs/`` — life-readable, ¬ durable
``notes/system/threads/``, ¬ ``dropbox/`` ingest staging.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_seat.registry import normalize_bus_address
from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.skill_delivery_channels import (
    text_without_inline_payload_regions,
)

# Capability-cell spelling; bus canonical is ``web-anthropic`` via
# ``normalize_bus_address`` (friction 24046 / agent-bus:5005#2).
WEB_RECEIVER_AGENT = "claude-web"
_LIFE_BUS_ADDRESS = normalize_bus_address(WEB_RECEIVER_AGENT)

# Life/web handoff packet + evidence mirrors only. Durable bus review
# sidecars stay under notes/system/threads/.
LIFE_HANDOFF_MIRROR_PREFIX = "ephemeral/handoffs"

_WORKSPACES_URI_RE = re.compile(
    r"workspaces://[^\s<>\)\]\"']+",
    re.IGNORECASE,
)

_POINTER_TEMPLATE_WORKSPACES = """\
{subject}

Read the packet:
  fs(sandbox="workspaces", op="read", path="{packet_path}")

The packet contains all six required blocks:
  <scope>, <invariants>, <task_guidance>, <mcp_capabilities>,
  <output_format>, <corpus>

Reply on this thread with findings. Use <need> only as last resort."""

_POINTER_TEMPLATE_CORTEX = """\
{subject}

Packet posture: LIFE/CORTEX MCP ON; CODE/VORTEX MCP OFF.
Read the cortex mirror through the life surface:
  fs(op="read", path="{packet_uri}")

Six blocks present; <mcp_capabilities> names permitted life-safe calls.
No workspaces sandbox, checkout/source access, or code-only tools.
Reply on this thread with findings. Use <need> only as last resort."""


@dataclass(frozen=True, slots=True)
class RecipientReachability:
    surface: str
    fs_sandboxes: tuple[str, ...]
    skill_delivery: str
    corpus_mode: str


WEB_RECIPIENT_REACHABILITY = RecipientReachability(
    surface="web-anthropic",
    fs_sandboxes=("cortex",),
    skill_delivery="inline_authoritative",
    corpus_mode="cortex_only",
)


def is_life_web_receiver(to_agent: str | None) -> bool:
    """True when *to_agent* is the life/web-anthropic handoff recipient.

    Admission posts as the canonical bus address (``web-anthropic``), not the
    capability-cell spelling (``claude-web``). Compare via
    ``normalize_bus_address`` so both forms (and roster aliases) match.
    """
    if not to_agent:
        return False
    return normalize_bus_address(to_agent) == _LIFE_BUS_ADDRESS


def _mirror_target_uri(workspaces_uri: str, *, thread_id: str | None) -> str:
    rel = workspaces_uri.split("://", 1)[-1].strip("/")
    if rel.startswith("universal-llm-gateway/"):
        rel = rel[len("universal-llm-gateway/") :]
    suffix = rel.replace("/", "-")
    if suffix.lower().endswith(".md"):
        suffix = suffix[:-3]
    thread_part = thread_id or "handoff"
    return f"cortex://{LIFE_HANDOFF_MIRROR_PREFIX}/{thread_part}-{suffix}.md"


def packet_mirror_uri(packet_path: str, *, thread_id: str | None = None) -> str:
    """Cortex URI for a workspaces packet_path (stem-based; thread optional)."""
    rel = packet_path.strip()
    for prefix in (
        "workspaces://universal-llm-gateway/",
        "workspaces://",
        "universal-llm-gateway/",
    ):
        if rel.startswith(prefix):
            rel = rel[len(prefix) :]
            break
    stem = Path(rel).name
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    thread_part = f"{thread_id}-" if thread_id else ""
    return f"cortex://{LIFE_HANDOFF_MIRROR_PREFIX}/{thread_part}{stem}.md"


def cortex_uri_to_rel_path(cortex_uri: str) -> str:
    if cortex_uri.startswith("cortex://"):
        return cortex_uri[len("cortex://") :]
    return cortex_uri.lstrip("/")


def mirror_packet_file_to_cortex(
    packet_file: Path,
    *,
    packet_path: str,
    thread_id: str | None = None,
    cortex_root: Path | None = None,
) -> str:
    """Copy packet bytes to cortex; return cortex:// URI."""
    uri = packet_mirror_uri(packet_path, thread_id=thread_id)
    rel = cortex_uri_to_rel_path(uri)
    root = (cortex_root or cortex_files_root()).resolve()
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        packet_file.read_text(encoding="utf-8", errors="replace"),
        encoding="utf-8",
    )
    return uri


def mirror_workspaces_pointers_for_web(
    text: str,
    *,
    thread_id: str | None = None,
    read_workspaces: Callable[[str], str] | None = None,
    write_cortex: Callable[[str, str], None] | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """Copy workspaces:// corpus pointers to cortex:// and rewrite in packet text."""
    rewrites: list[tuple[str, str]] = []
    scan_text = text_without_inline_payload_regions(text)
    out = text
    pending: list[tuple[int, str, str]] = []
    for match in _WORKSPACES_URI_RE.finditer(scan_text):
        ws_uri = match.group(0).rstrip(".,;)")
        cortex_uri = _mirror_target_uri(ws_uri, thread_id=thread_id)
        if read_workspaces is None or write_cortex is None:
            rewrites.append((ws_uri, cortex_uri))
            pending.append((match.start(), ws_uri, cortex_uri))
            continue
        try:
            body = read_workspaces(ws_uri)
            write_cortex(cortex_uri, body)
            rewrites.append((ws_uri, cortex_uri))
            pending.append((match.start(), ws_uri, cortex_uri))
        except OSError as exc:
            raise ValueError(
                f"pointer_unresolvable_on_seat: cannot mirror {ws_uri!r}: {exc}"
            ) from exc
    for start, ws_uri, cortex_uri in sorted(pending, reverse=True):
        out = out[:start] + cortex_uri + out[start + len(ws_uri) :]
    return out, rewrites


def build_life_mirror_pointer_body(
    *,
    subject: str,
    packet_uri: str,
) -> str:
    """Bus pointer body that instructs a cortex read (life-readable)."""
    return _POINTER_TEMPLATE_CORTEX.format(subject=subject, packet_uri=packet_uri)


def build_workspaces_pointer_body(
    *,
    subject: str,
    packet_path: str,
) -> str:
    """Bus pointer body for workspaces-capable receivers (cursor / code)."""
    return _POINTER_TEMPLATE_WORKSPACES.format(subject=subject, packet_path=packet_path)
