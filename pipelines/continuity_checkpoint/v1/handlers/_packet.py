"""Render pre-consolidate cursor-sdk packet from prompts.yaml."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

PRE_CONSOLIDATE_TAPE_CHARS = int(os.environ.get("PRE_CONSOLIDATE_TAPE_CHARS", "120000"))


def _repo_root() -> Path:
    for key in ("ULG_REPO_ROOT", "REPO_ROOT", "GIT_WORK_TREE"):
        val = os.environ.get(key, "").strip()
        if val:
            return Path(val)
    return Path(__file__).resolve().parents[4]


def _prompts_path() -> Path:
    return Path(__file__).resolve().parent.parent / "prompts.yaml"


def render_tape_lines(
    tape_json: dict[str, Any] | None,
    *,
    max_chars: int = PRE_CONSOLIDATE_TAPE_CHARS,
) -> tuple[str, dict[str, Any]]:
    """Render envelope messages as turn-delimited role/content blocks."""
    stats: dict[str, Any] = {
        "kept_turns": 0,
        "dropped_turns": 0,
        "chars": 0,
        "truncated": False,
    }
    if not tape_json:
        return "(tape unavailable)", stats

    envelope = tape_json.get("envelope") if isinstance(tape_json, dict) else None
    if not isinstance(envelope, dict):
        envelope = tape_json if isinstance(tape_json, dict) else {}
    messages = envelope.get("messages") or []
    if not isinstance(messages, list):
        return "(tape unavailable)", stats

    blocks: list[tuple[Any, str, str]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "?")
        if role == "index":
            continue
        turn_index = msg.get("turn_index", "?")
        content = str(msg.get("content") or "")
        blocks.append((turn_index, role, content))

    if not blocks:
        return "(tape empty)", stats

    rendered_blocks = [
        f"--- t{turn_index} {role} ---\n{content}"
        for turn_index, role, content in blocks
    ]
    full = "\n\n".join(rendered_blocks)
    if len(full) <= max_chars:
        stats.update(
            kept_turns=len(blocks),
            dropped_turns=0,
            chars=len(full),
            truncated=False,
        )
        return full, stats

    dropped = 0
    while rendered_blocks and len("\n\n".join(rendered_blocks)) > max_chars:
        rendered_blocks.pop(0)
        dropped += 1

    oldest_kept = "t?"
    if rendered_blocks:
        header = rendered_blocks[0].split("\n", 1)[0]
        oldest_kept = header.replace("--- ", "").split(" ---")[0]
    marker = (
        f"(tape truncated: dropped {dropped} oldest of {len(blocks)} turns; "
        f"oldest kept {oldest_kept})"
    )
    result = marker + "\n\n" + "\n\n".join(rendered_blocks)
    stats.update(
        kept_turns=len(rendered_blocks),
        dropped_turns=dropped,
        chars=len(result),
        truncated=True,
    )
    return result, stats


def render_pre_consolidate_packet(
    *,
    thread: str,
    seal: dict[str, Any],
    tape_summary: str,
    tape_lines: str,
    tip_residue: str,
    resume_open: str,
    pools_section: str,
    hub_summary: str,
    seat_seed: str,
) -> str:
    """Fill the pre_consolidate_packet template with handler inputs."""
    raw = yaml.safe_load(_prompts_path().read_text(encoding="utf-8"))
    template = raw["prompts"]["continuity_checkpoint.v1.pre_consolidate_packet"]["template"]
    subs = {
        "{{ thread }}": thread,
        "{{ seal_json }}": json.dumps(seal, indent=2, default=str)[:4000],
        "{{ tape_stats }}": tape_summary[:2000],
        "{{ tape_lines }}": tape_lines,
        "{{ tip_residue }}": tip_residue[:1200],
        "{{ resume_open }}": resume_open[:2000],
        "{{ pools_section }}": pools_section[:2000],
        "{{ hub_summary }}": hub_summary[:2000],
        "{{ seat_seed }}": seat_seed[:900],
    }
    out = template
    for key, val in subs.items():
        out = out.replace(key, val)
    return out


def write_packet_file(*, thread: str, execution_id: str, content: str) -> str | None:
    """Write packet under repo tmp/prompts; return path relative to repo root."""
    root = _repo_root()
    out_dir = root / "tmp" / "prompts"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"continuity-cp-{thread}-{execution_id[:8]}.md"
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(root))
    except OSError:
        return None


__all__ = [
    "PRE_CONSOLIDATE_TAPE_CHARS",
    "render_pre_consolidate_packet",
    "render_tape_lines",
    "write_packet_file",
]
