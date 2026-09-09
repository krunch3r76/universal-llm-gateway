"""Render pre-consolidate cursor-sdk packet from prompts.yaml."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    import os

    for key in ("ULG_REPO_ROOT", "REPO_ROOT", "GIT_WORK_TREE"):
        val = os.environ.get(key, "").strip()
        if val:
            return Path(val)
    return Path(__file__).resolve().parents[4]


def _prompts_path() -> Path:
    return Path(__file__).resolve().parent.parent / "prompts.yaml"


def render_pre_consolidate_packet(
    *,
    thread: str,
    seal: dict[str, Any],
    tape_summary: str,
    tip_residue: str,
    resume_open: str,
    pools_section: str,
    hub_summary: str,
    seat_residue: str,
) -> str:
    """Fill the pre_consolidate_packet template with handler inputs."""
    raw = yaml.safe_load(_prompts_path().read_text(encoding="utf-8"))
    template = raw["prompts"]["continuity_checkpoint.v1.pre_consolidate_packet"]["template"]
    subs = {
        "{{ thread }}": thread,
        "{{ seal_json }}": json.dumps(seal, indent=2, default=str)[:4000],
        "{{ tape_summary }}": tape_summary[:2000],
        "{{ tip_residue }}": tip_residue[:1200],
        "{{ resume_open }}": resume_open[:2000],
        "{{ pools_section }}": pools_section[:2000],
        "{{ hub_summary }}": hub_summary[:2000],
        "{{ seat_residue }}": seat_residue[:900],
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


__all__ = ["render_pre_consolidate_packet", "write_packet_file"]
