"""Load roster YAML for scheduled extract.

The live roster names real people, so it lives outside the repo — this tree is
tracked against a public remote. Tracked seed: `config/unclaimed_property_hunter/
roster.example.yaml` (placeholders only). Same split as the model-rates catalog.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROSTER_PATH_ENV = "UNCLAIMED_PROPERTY_ROSTER_PATH"
EXAMPLE_ROSTER_REL = "config/unclaimed_property_hunter/roster.example.yaml"
_DEFAULT_ROSTER_REL = Path(".gateway/unclaimed_property_hunter/roster.yaml")


def default_roster_path() -> Path:
    """Out-of-repo roster location, overridable by env."""
    override = os.environ.get(ROSTER_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / _DEFAULT_ROSTER_REL


@dataclass(frozen=True)
class RosterSubject:
    surname: str
    also: tuple[str, ...]


@dataclass(frozen=True)
class HunterRoster:
    subjects: tuple[RosterSubject, ...]
    zip_cache: Path


def load_roster(path: Path) -> HunterRoster:
    """Parse roster config; empty subjects are allowed (caller handles roster_empty)."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_subjects = data.get("subjects") or []
    subjects: list[RosterSubject] = []
    for row in raw_subjects:
        surname = str(row.get("surname", "")).strip()
        if not surname:
            raise ValueError(f"roster {path} subject missing surname")
        also = tuple(str(x).strip() for x in (row.get("also") or []) if str(x).strip())
        subjects.append(RosterSubject(surname=surname, also=also))
    zip_cache = Path(str(data.get("zip_cache", "")).strip())
    if not str(zip_cache):
        raise ValueError(f"roster {path} missing zip_cache")
    return HunterRoster(subjects=tuple(subjects), zip_cache=zip_cache)
