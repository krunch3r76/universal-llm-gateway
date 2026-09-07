"""Shared helpers for the CDP mock hop reactor night script."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COMPOSER_READY_RE = re.compile(
    r"^COMPOSER_READY:(10160|10094):(.+)$",
    re.MULTILINE,
)
NEXT_LEG_RE = re.compile(r"^NEXT_LEG:\s*(.+)$", re.MULTILINE)


@dataclass
class ComposerBudget:
    count: int = 0
    max_per_todo: int = 2
    last_execution_id: str | None = None
    last_task: str | None = None


@dataclass
class ReactorState:
    fable_episode: int = 0
    fable_chat_url: str | None = None
    fable_stargate_execution_id: str | None = None
    fable_satellite_execution_id: str | None = None
    fable_registration_id: str | None = None
    fable_last_turn_ordinal: int | None = None
    fable_thread: str = ""
    last_harvest_seq: int = 0
    composer: dict[str, ComposerBudget] = field(
        default_factory=lambda: {
            "10160": ComposerBudget(),
            "10094": ComposerBudget(),
        }
    )
    pending_composer: list[dict[str, str]] = field(default_factory=list)
    last_next_leg: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ReactorState:
        data = dict(raw)
        composer_raw = data.pop("composer", {})
        composer: dict[str, ComposerBudget] = {}
        for key in ("10160", "10094"):
            block = composer_raw.get(key) or {}
            composer[key] = ComposerBudget(
                count=int(block.get("count") or 0),
                max_per_todo=int(block.get("max_per_todo") or 2),
                last_execution_id=block.get("last_execution_id"),
                last_task=block.get("last_task"),
            )
        allowed = {
            "fable_episode",
            "fable_chat_url",
            "fable_stargate_execution_id",
            "fable_satellite_execution_id",
            "fable_registration_id",
            "fable_last_turn_ordinal",
            "fable_thread",
            "last_harvest_seq",
            "pending_composer",
            "last_next_leg",
        }
        kwargs = {k: v for k, v in data.items() if k in allowed}
        return cls(composer=composer, **kwargs)


def load_state(path: Path) -> ReactorState:
    if not path.is_file():
        return ReactorState()
    return ReactorState.from_json(json.loads(path.read_text()))


def save_state(path: Path, state: ReactorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_json(), indent=2) + "\n")


def format_harvest_turns(turns: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for turn in turns:
        author = str(turn.get("author") or "?")
        text = str(turn.get("text") or "").strip()
        if text:
            chunks.append(f"### {author}\n\n{text}")
    return "\n\n---\n\n".join(chunks)


def save_harvest(
    harvest_dir: Path,
    seq: int,
    *,
    execution_id: str | None,
    outcome: str,
    body: str,
) -> Path:
    harvest_dir.mkdir(parents=True, exist_ok=True)
    path = harvest_dir / f"{seq:04d}.md"
    header = (
        f"---\nepisode: {seq}\nexecution_id: {execution_id or ''}\n"
        f"outcome: {outcome}\nharvested_at: {datetime.now(UTC).isoformat(timespec='seconds')}\n---\n\n"
    )
    path.write_text(header + body)
    return path


def load_harvest_chain(harvest_dir: Path, depth: int, after_seq: int) -> list[str]:
    if not harvest_dir.is_dir():
        return []
    paths = sorted(harvest_dir.glob("*.md"))
    selected = [p for p in paths if p.stem.isdigit() and int(p.stem) <= after_seq]
    selected = selected[-depth:]
    return [p.read_text() for p in selected]


def parse_composer_signals(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in COMPOSER_READY_RE.finditer(text):
        todo_id, task = match.group(1), match.group(2).strip()
        if task:
            found.append((todo_id, task))
    return found


def parse_next_leg(text: str) -> str | None:
    matches = NEXT_LEG_RE.findall(text)
    return matches[-1].strip() if matches else None


def append_composer_task(state: ReactorState, todo_id: str, task: str) -> None:
    key = (todo_id, task)
    seen = {(item["todo_id"], item["task"]) for item in state.pending_composer}
    if key in seen:
        return
    state.pending_composer.append({"todo_id": todo_id, "task": task})


def night_runner_enabled(*, cli_flag: bool = False) -> bool:
    if cli_flag:
        return True
    token = os.environ.get("CDP_HOP_NIGHT_RUNNER", "").strip().lower()
    return token in {"1", "true", "yes"}


def build_successor_prompt(
    *,
    episode: int,
    charter_path: Path,
    harvest_chain: list[str],
    composer_notes: list[str],
    last_next_leg: str | None,
    steer_hint: str | None = None,
) -> str:
    charter = charter_path.read_text() if charter_path.is_file() else ""
    parts = [
        f"# Fable successor episode {episode}",
        "",
        "The prior CSE stream ended. Continue from harvested context below.",
        "",
    ]
    if steer_hint:
        parts += ["## Backup steer (binding)", "", steer_hint, ""]
    if last_next_leg:
        parts += ["## Prior NEXT_LEG", "", last_next_leg, ""]
    if harvest_chain:
        parts += ["## Prior harvest chain (newest last)", ""]
        for idx, block in enumerate(harvest_chain, start=1):
            parts += [f"### Harvest {idx}", "", block, ""]
    if composer_notes:
        parts += ["## Composer outcomes since last episode", ""]
        parts.extend(composer_notes)
        parts.append("")
    parts += ["## Charter", "", charter, ""]
    parts += [
        "## Close this episode",
        "",
        "- Emit `COMPOSER_READY:<10160|10094>:<task>` when a bounded implement slice is ready.",
        "- End with `NEXT_LEG: <one-line handoff>`.",
        "",
    ]
    return "\n".join(parts)
