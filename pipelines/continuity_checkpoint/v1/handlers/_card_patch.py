"""Validate worker JSON and patch continuity card sections."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from markdown_sections import read_section, replace_section

_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def parse_worker_json(text: str) -> dict[str, Any] | None:
    """Extract pre-consolidate JSON from worker closeout text."""
    match = _JSON_FENCE_RE.search(text)
    raw = match.group(1) if match else text.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if "card_patch" in data:
        return data
    if data.get("schema_version") == 1:
        return None
    return data


def validate_worker_payload(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate pre-consolidate worker deliverable shape."""
    residue = str(data.get("residue") or "")
    mission = str(data.get("mission") or "")
    if "### User" in residue or "### User" in mission:
        return False, "speech_in_body"
    if len(residue) > 800:
        return False, "residue_too_long"
    if "Mission:" not in residue and not mission.strip():
        return False, "missing_mission"
    card_patch = data.get("card_patch")
    if not isinstance(card_patch, dict):
        return False, "missing_card_patch"
    if not str(card_patch.get("resume_open") or "").strip():
        return False, "missing_resume_open"
    return True, "ok"


def _card_path(thread: str) -> Path | None:
    root_env = os.environ.get("CORTEX_FILES_ROOT")
    if not root_env:
        try:
            from cortex_store.dispatch_ops._shared import _FILES_ROOT

            files_root = _FILES_ROOT
        except Exception:  # noqa: BLE001
            return None
    else:
        files_root = Path(root_env)
    path = files_root / "notes" / "system" / "threads" / f"{thread}-continuity.md"
    return path if path.parent.is_dir() or path.is_file() else None


def apply_card_patch(
    *,
    thread: str,
    resume_open: str,
    opportunities_rows: list[str],
) -> tuple[bool, str, str]:
    """Patch ## Resume open and append opportunities rows. Returns applied, uri, reason."""
    card_uri = f"cortex://notes/system/threads/{thread}-continuity.md"
    card_path = _card_path(thread)
    if card_path is None or not card_path.is_file():
        return False, card_uri, "files_root_unreachable"
    text = card_path.read_text(encoding="utf-8")
    try:
        updated, _ = replace_section(text, "Resume open", resume_open.strip() + "\n")
    except Exception:  # noqa: BLE001
        return False, card_uri, "resume_open_patch_failed"
    card_path.write_text(updated, encoding="utf-8")

    if opportunities_rows:
        opp_path = card_path.parent / f"{thread}-opportunities.md"
        if opp_path.is_file():
            opp_text = opp_path.read_text(encoding="utf-8")
            try:
                existing = read_section(opp_text, "Opportunities").strip()
            except Exception:  # noqa: BLE001
                existing = ""
            rows = [r for r in opportunities_rows if r.strip()]
            if rows:
                block = existing + ("\n" if existing else "") + "\n".join(f"- {r}" for r in rows)
                try:
                    opp_updated, _ = replace_section(opp_text, "Opportunities", block + "\n")
                    opp_path.write_text(opp_updated, encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
    return True, card_uri, "ok"


__all__ = ["apply_card_patch", "parse_worker_json", "validate_worker_payload"]
