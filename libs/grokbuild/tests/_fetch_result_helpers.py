"""Shared helpers for fetch_result test modules."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


def write_sidecar(
    sidecar_root: Path,
    dispatch_id: str,
    records: list[dict[str, Any]],
) -> None:
    sidecar_root.mkdir(parents=True, exist_ok=True)
    path = sidecar_root / f"{dispatch_id}.ndjson"
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def completed_records(
    *,
    cwd: str = "/tmp/repo",
    dispatch_id: str = "fetch-ok",
    exit_code: int | None = 0,
    status: str = "completed",
) -> list[dict[str, Any]]:
    start = now_ms() - 1000
    return [
        {
            "phase": "started",
            "ts": start,
            "argv": [
                "/usr/bin/grok",
                "-p",
                "prompt",
                "--cwd",
                cwd,
                "--output-format",
                "json",
                "--permission-mode",
                "plan",
                "--always-approve",
            ],
            "cwd": cwd,
            "mode": "read_only",
            "permission_mode": "plan",
            "model": "grok-4.3",
            "session_id": None,
            "continue_recent": False,
            "output_format": "json",
            "git_status_pre": "",
            "dirty_admission": False,
        },
        {"phase": "stdout_chunk", "ts": start + 200, "data": "hello"},
        {"phase": "stderr_chunk", "ts": start + 300, "data": "warning\n"},
        {
            "phase": "exit",
            "ts": start + 1000,
            "status": status,
            "exit_code": exit_code,
            "duration_s": 1.0,
            "git_status_post": "",
            "git_diff_stat": "",
            "audit_incomplete": False,
            "sidecar_gaps": 0,
        },
    ]
