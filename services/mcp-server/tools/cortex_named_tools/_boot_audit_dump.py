"""Per-boot audit dump — durable record of what bytes reached the agent.

Writes a Markdown sidecar to /data/files/notes/system/audit/boots/{filename}.md
containing the verbatim briefing card, operational context, manifest JSON,
and a fetch ledger. Indexed by RAG under scope 'boot_snapshots' for
historical drift queries.

Filename is decoupled from session_id: session_id is
`{agent}-%Y-%m-%d-%H%M%S-{3hex}` (second resolution + entropy suffix) for
entity-id uniqueness, while the audit dump uses `{agent}-%Y-%m-%d-%H%M%S`
(second resolution only) as a filesystem-safe write key.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text
from mcp_events import record
from universal_logging import get_logger

logger = get_logger(__name__)

AUDIT_DIR = Path("/data/files/notes/system/audit/boots")


def write_audit_dump(
    session_id: str,
    agent: str,
    boot_time: datetime,
    card: str,
    ops_context: str,
    artifacts: list,  # list[InjectedArtifact]
    transcript_continuation: dict[str, Any] | None,
) -> str | None:
    """Write a Markdown sidecar capturing the boot's full injection state.

    Returns the dump path on success, or None on failure (with
    `mcp.cortex.boot.dump.failed` event emitted; the dump is best-effort
    and must not fail the boot itself).
    """
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        record(
            "mcp.cortex.boot.dump.failed",
            session_id=session_id,
            error=str(exc),
            error_type=type(exc).__name__,
            stage="mkdir",
        )
        logger.warning("Could not create audit dir %s: %s", AUDIT_DIR, exc)
        return None

    # Audit filename uses second-resolution timestamp, decoupled from
    # session_id (which carries a 3-hex entropy suffix for entity uniqueness).
    fname = f"{agent}-{boot_time.strftime('%Y-%m-%d-%H%M%S')}.md"
    out_path = AUDIT_DIR / fname
    body = _render_dump(
        session_id, agent, card, ops_context, artifacts, transcript_continuation
    )
    try:
        durable_write_text(out_path, body, retain_store_root=Path("/data/files"))
    except OSError as exc:
        record(
            "mcp.cortex.boot.dump.failed",
            session_id=session_id,
            error=str(exc),
            error_type=type(exc).__name__,
            stage="write_text",
            path=str(out_path),
        )
        logger.warning("Could not write audit dump %s: %s", out_path, exc)
        return None

    record(
        "mcp.cortex.boot.dump.written",
        session_id=session_id,
        path=str(out_path),
        bytes=len(body.encode("utf-8")),
    )
    return str(out_path)


def _render_dump(
    session_id: str,
    agent: str,
    card: str,
    ops_context: str,
    artifacts: list,
    transcript_continuation: dict[str, Any] | None,
) -> str:
    parts = [
        f"# Boot Audit — {session_id}",
        f"\n**Agent**: {agent}",
        f"\n**Continuation**: {transcript_continuation['entity_id'] if transcript_continuation else 'none'}",
        "\n## Manifest",
        "```json",
        json.dumps([asdict(a) for a in artifacts], indent=2, default=str),
        "```",
        "\n## Briefing Card (verbatim)",
        "```markdown",
        card,
        "```",
        "\n## Operational Context (verbatim)",
        "```markdown",
        ops_context,
        "```",
        "\n## Card Block Ledger",
        _render_block_ledger(card),
        "\n## Fetch Ledger",
        _render_fetch_ledger(artifacts),
    ]
    return "\n".join(parts)


def _render_fetch_ledger(artifacts: list) -> str:
    lines = [
        "| Artifact | Tool | Rows | Bytes | Duration ms |",
        "|---|---|---|---|---|",
    ]
    for art in artifacts:
        for fetch in art.fetches:
            lines.append(
                f"| {art.name} | `{fetch.tool}` | {fetch.rows} | "
                f"{fetch.bytes} | {fetch.duration_ms} |"
            )
    return "\n".join(lines) if len(lines) > 2 else "_(no fetches recorded)_"


def _render_block_ledger(card: str) -> str:
    """Per-`## `-block UTF-8 byte table — makes byte arguments one fs-read."""
    total = len(card.encode("utf-8"))
    lines = ["| Block | Bytes | % |", "|---|---|---|"]
    for block in re.split(r"(?m)^(?=## )", card):
        name = block.split("\n", 1)[0][:60] if block.startswith("##") else "(title)"
        n = len(block.encode("utf-8"))
        pct = (100 * n / total) if total else 0.0
        lines.append(f"| {name} | {n} | {pct:.1f} |")
    lines.append(f"| **TOTAL** | **{total}** | 100.0 |")
    return "\n".join(lines)
