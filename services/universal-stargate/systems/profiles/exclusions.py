"""Persistent model exclusions — task-scoped shitlist.

Loads ~/.gateway/model-exclusions.yaml and provides a lookup of model IDs
to exclude from selection for a given task. The _global key applies to all tasks.

File format:
    code_review:
      - mistral-large-2407
    planning:
      - o3
    _global:
      - some/broken-model
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_EXCLUSIONS_PATH = Path.home() / ".gateway" / "model-exclusions.yaml"


def load_exclusions(
    path: Path = _EXCLUSIONS_PATH,
) -> dict[str, list[str]]:
    """Load exclusions YAML, returning {task: [model_ids]}.

    Returns empty dict if file doesn't exist or is malformed.
    """
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            logger.warning("model-exclusions.yaml: expected dict, got %s", type(raw))
            return {}
        result: dict[str, list[str]] = {}
        for key, value in raw.items():
            if isinstance(value, list):
                result[str(key)] = [str(v) for v in value]
        return result
    except Exception:
        logger.exception("Failed to load model-exclusions.yaml from %s", path)
        return {}


def get_excluded_models(
    task: str,
    exclusions: dict[str, list[str]],
) -> frozenset[str]:
    """Get excluded model IDs for a task, including _global entries."""
    task_excluded = exclusions.get(task, [])
    global_excluded = exclusions.get("_global", [])
    return frozenset(task_excluded) | frozenset(global_excluded)


def add_exclusion(
    task: str,
    model_id: str,
    path: Path = _EXCLUSIONS_PATH,
) -> None:
    """Add a model to the exclusion list for a task."""
    exclusions = load_exclusions(path)
    task_list = exclusions.setdefault(task, [])
    if model_id not in task_list:
        task_list.append(model_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(exclusions, default_flow_style=False, sort_keys=True))
    logger.info("Added exclusion: task=%s model=%s", task, model_id)


def remove_exclusion(
    task: str,
    model_id: str,
    path: Path = _EXCLUSIONS_PATH,
) -> bool:
    """Remove a model from the exclusion list. Returns True if found and removed."""
    exclusions = load_exclusions(path)
    task_list = exclusions.get(task, [])
    if model_id not in task_list:
        return False
    task_list.remove(model_id)
    if not task_list:
        del exclusions[task]
    path.write_text(yaml.dump(exclusions, default_flow_style=False, sort_keys=True))
    logger.info("Removed exclusion: task=%s model=%s", task, model_id)
    return True
