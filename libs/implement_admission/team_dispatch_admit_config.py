"""Load ``model_defaults`` from team-dispatch-admit pipeline YAML (hot-reload friendly)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ADMIT_YAML = (
    Path(__file__).resolve().parents[2]
    / "pipelines/team_dispatch_admit/v1/team-dispatch-admit-v1.yaml"
)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=4)
def _load_at_mtime(mtime_ns: int) -> dict[str, Any]:
    del mtime_ns  # part of cache key only
    return _read_yaml(_ADMIT_YAML)


def load_team_dispatch_admit_pipeline_raw() -> dict[str, Any]:
    """Return parsed team-dispatch-admit-v1.yaml; refreshes when mtime changes."""
    return _load_at_mtime(_ADMIT_YAML.stat().st_mtime_ns)


def load_model_defaults_block() -> dict[str, Any]:
    """Top-level ``model_defaults:`` mapping (may be empty)."""
    raw = load_team_dispatch_admit_pipeline_raw()
    block = raw.get("model_defaults")
    return dict(block) if isinstance(block, dict) else {}


def load_conductor_model_defaults() -> dict[str, Any]:
    """``model_defaults.conductor`` when present."""
    block = load_model_defaults_block()
    conductor = block.get("conductor")
    return dict(conductor) if isinstance(conductor, dict) else {}
