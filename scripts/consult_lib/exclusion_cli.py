"""CLI wrappers for model exclusion and ranking management."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import yaml

_EXCLUSIONS_PATH = Path.home() / ".gateway" / "model-exclusions.yaml"


def show_exclusions() -> None:
    """Print current model exclusions."""
    if not _EXCLUSIONS_PATH.exists():
        print(f"No exclusions file at {_EXCLUSIONS_PATH}")
        return
    raw = yaml.safe_load(_EXCLUSIONS_PATH.read_text())
    if not raw:
        print("No exclusions configured.")
        return
    for task, models in sorted(raw.items()):
        print(f"\n{task}:")
        for model in models:
            print(f"  - {model}")


def add_exclusion_cli(task: str, model_id: str) -> None:
    """Add a model exclusion and confirm."""
    exclusions: dict[str, list[str]] = {}
    if _EXCLUSIONS_PATH.exists():
        exclusions = yaml.safe_load(_EXCLUSIONS_PATH.read_text()) or {}

    task_list = exclusions.setdefault(task, [])
    if model_id in task_list:
        print(f"Already excluded: {model_id} for task '{task}'")
        return

    task_list.append(model_id)
    _EXCLUSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _EXCLUSIONS_PATH.write_text(
        yaml.dump(exclusions, default_flow_style=False, sort_keys=True)
    )
    print(f"Excluded {model_id} from task '{task}'")


def remove_exclusion_cli(task: str, model_id: str) -> None:
    """Remove a model exclusion and confirm."""
    if not _EXCLUSIONS_PATH.exists():
        print(f"No exclusions file at {_EXCLUSIONS_PATH}")
        return

    exclusions = yaml.safe_load(_EXCLUSIONS_PATH.read_text()) or {}
    task_list = exclusions.get(task, [])
    if model_id not in task_list:
        print(f"Not excluded: {model_id} for task '{task}'")
        return

    task_list.remove(model_id)
    if not task_list:
        del exclusions[task]
    _EXCLUSIONS_PATH.write_text(
        yaml.dump(exclusions, default_flow_style=False, sort_keys=True)
    )
    print(f"Removed exclusion: {model_id} from task '{task}'")


def show_rankings(task: str, stargate_url: str) -> None:
    """Fetch and display model rankings for a task."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{stargate_url.rstrip('/')}/v1/models/select/rankings",
                params={"task": task},
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(
            f"Rankings endpoint error: {exc.response.status_code}",
            file=sys.stderr,
        )
        return
    except httpx.RequestError as exc:
        print(f"Rankings endpoint unavailable: {exc}", file=sys.stderr)
        return

    data = resp.json()
    rankings = data.get("rankings", [])
    exclusions = data.get("exclusions", [])

    print(f"\nModel rankings for task: {task}")
    print(f"{'Model':<45} {'Score':>6} {'Obs':>4} {'Conf':>5} {'Excluded':>8}")
    print("-" * 75)

    for entry in rankings:
        model = entry["model_id"]
        if len(model) > 44:
            model = model[:41] + "..."
        excluded = "YES" if entry.get("excluded") else ""
        print(
            f"{model:<45} {entry['score']:>6.3f} "
            f"{entry['observations']:>4.0f} "
            f"{entry['confidence']:>5.2f} "
            f"{excluded:>8}"
        )

    if exclusions:
        print(f"\nExcluded models: {', '.join(exclusions)}")
