"""Append-only charter-runner transcripts under ``/tmp/logs/charter-runner/``.

Files are numbered by agent-bus thread id:
- ``{worker_thread}.log`` — one window (admit packet + worker turns + CHECKPOINT)
- ``root-{root_id}.log`` — arc index lines pointing at each window file

Transcripts are ephemeral (``/tmp/logs``). Harvest-done markers live in a
durable dir outside ``/tmp`` so a manage restart does not re-emit ``closed``
or re-close historical workers (A-R3-3).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOG_DIR = Path("/tmp/logs/charter-runner")
_HARVESTED_DIR = Path.home() / ".local" / "share" / "charter-runner" / "harvested"


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _HARVESTED_DIR.mkdir(parents=True, exist_ok=True)


def worker_transcript_path(worker_thread: str) -> Path:
    """``/tmp/logs/charter-runner/{agent-bus-id}.log``."""
    return LOG_DIR / f"{worker_thread}.log"


def root_index_path(root_id: str) -> Path:
    return LOG_DIR / f"root-{root_id}.log"


def _append(path: Path, text: str) -> None:
    _ensure_dirs()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


def harvest_marker(root_id: str, window_index: int) -> Path:
    return _HARVESTED_DIR / f"{root_id}-w{window_index}.done"


def already_harvested(root_id: str, window_index: int) -> bool:
    return harvest_marker(root_id, window_index).exists()


def mark_harvested(root_id: str, window_index: int) -> None:
    _ensure_dirs()
    harvest_marker(root_id, window_index).write_text(_stamp() + "\n", encoding="utf-8")


def append_admit(
    *,
    root_id: str,
    window_index: int,
    worker_thread: str,
    packet_path: str,
    packet_text: str,
    push_reminder: str = "",
    dispatch_id: str = "",
) -> Path:
    """Record one admitted window into ``{worker_thread}.log``."""
    if not worker_thread:
        worker_thread = f"unknown-w{window_index}"
    path = worker_transcript_path(worker_thread)
    banner = (
        f"\n{'=' * 72}\n"
        f"ADMIT  {_stamp()}  agent-bus:{worker_thread}  "
        f"root={root_id}  window={window_index}\n"
        f"dispatch_id={dispatch_id or worker_thread}\n"
        f"packet_path={packet_path}\n"
    )
    if push_reminder:
        banner += f"push_reminder={push_reminder}\n"
    banner += f"{'-' * 72}\n--- packet ---\n"
    _append(path, banner + packet_text.rstrip() + "\n")
    _append(
        root_index_path(root_id),
        f"{_stamp()} ADMIT window={window_index} "
        f"agent-bus:{worker_thread} -> {path}\n",
    )
    return path


def append_closeout(
    *,
    root_id: str,
    window_index: int,
    worker_thread: str,
    checkpoint_subject: str,
    checkpoint_body: str,
    worker_turns: list[dict[str, Any]],
    worker_closed: bool | None = None,
) -> Path:
    """Append worker-thread turns + root CHECKPOINT into ``{worker_thread}.log``."""
    if not worker_thread:
        worker_thread = f"unknown-w{window_index}"
    path = worker_transcript_path(worker_thread)
    lines = [
        f"\n{'=' * 72}",
        f"CLOSEOUT  {_stamp()}  agent-bus:{worker_thread}  "
        f"root={root_id}  window={window_index}",
        f"checkpoint_subject={checkpoint_subject}",
    ]
    if worker_closed is not None:
        lines.append(f"worker_thread_closed={worker_closed}")
    lines.extend([f"{'-' * 72}", "--- worker turns ---"])
    if not worker_turns:
        lines.append("(no worker turns fetched)")
    for turn in worker_turns:
        n = turn.get("turn_number", "?")
        frm = turn.get("from", "?")
        to = turn.get("to", "?")
        subj = turn.get("subject") or ""
        body = str(turn.get("body") or "").rstrip()
        lines.append(f"\n### t{n} {frm} → {to}: {subj}")
        if body:
            lines.append(body)
    lines.extend(
        [
            f"\n{'-' * 72}",
            "--- root CHECKPOINT ---",
            checkpoint_body.rstrip(),
            "",
        ]
    )
    _append(path, "\n".join(lines))
    _append(
        root_index_path(root_id),
        f"{_stamp()} CLOSEOUT window={window_index} "
        f"agent-bus:{worker_thread} closed={worker_closed} -> {path}\n",
    )
    mark_harvested(root_id, window_index)
    return path


def append_executor_note(worker_thread: str, executor: dict[str, Any]) -> None:
    """Append the executor bind to the worker transcript, if one was recorded.

    Writes a ``--- executor ---`` block carrying seat/model/knobs/contract so the
    numbered transcript records which substrate ran the window. A missing thread id
    or empty executor mapping is a no-op (nothing to record).
    """
    if not executor or not worker_thread:
        return
    note = (
        f"\n--- executor ---\n"
        f"seat={executor.get('seat')} model={executor.get('model')} "
        f"knobs={executor.get('model_knobs')} "
        f"contract={executor.get('contract')}\n"
    )
    _append(worker_transcript_path(worker_thread), note)


def parse_admission_meta(body: str | None) -> dict[str, Any]:
    """Best-effort JSON from an admission-pointer turn body."""
    try:
        data = json.loads(str(body or ""))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def dispatch_id_from_transcript(worker_thread: str) -> str | None:
    """Read ``dispatch_id=`` from the worker transcript banner when present."""
    if not worker_thread:
        return None
    path = worker_transcript_path(worker_thread)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("dispatch_id="):
            val = line.split("=", 1)[1].strip()
            return val or None
    return None
