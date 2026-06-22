"""Scheduled read-only skill-graph drift monitor for cortex-api.

Follows the agent-bus watchdog pattern (``agent_bus_store/watchdog.py``):
asyncio background task started from FastAPI lifespan, env-configured sweep
interval, fire-and-forget observability via ``event_publisher.record()``.

Read-only: runs ``ingest_skills.py --check --report`` on a cadence. Never
mutates the graph; reconciliation stays ``make skill-graph-reconcile``.

Hysteresis: alert only when ``drift_count > edge_threshold`` OR
``consecutive_dirty_runs >= run_threshold``. A single transient drift does not
page. State resets on a clean run.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_logging import get_logger

from .event_publisher import (
    cortex_skill_graph_drift_alert,
    cortex_skill_graph_drift_checked,
    cortex_skill_graph_drift_sweep_failed,
)

logger = get_logger("cortex-api.skill_graph_drift_monitor")

_SWEEP_INTERVAL: int = int(os.getenv("CORTEX_SKILL_GRAPH_DRIFT_INTERVAL", "3600"))
_EDGE_THRESHOLD: int = int(os.getenv("CORTEX_SKILL_GRAPH_DRIFT_EDGE_THRESHOLD", "1"))
_RUN_THRESHOLD: int = int(os.getenv("CORTEX_SKILL_GRAPH_DRIFT_RUN_THRESHOLD", "2"))
_ALERT_THREAD: str = os.getenv("CORTEX_SKILL_GRAPH_DRIFT_ALERT_THREAD", "2763")
_ALERT_FROM: str = os.getenv("CORTEX_SKILL_GRAPH_DRIFT_ALERT_FROM", "cortex-api")
_AGENT_BUS_URL = DEFAULT_AGENT_BUS_URL


@dataclass
class _MonitorState:
    consecutive_dirty_runs: int = 0
    last_clean_ts: str | None = None
    alert_active: bool = False


_state = _MonitorState()


def _repo_root() -> Path:
    env = os.environ.get("ULG_REPO_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "scripts/cortex/ingest_skills.py").is_file():
        return candidate
    return candidate


def _agent_bus_token() -> str:
    return os.environ.get("AGENT_BUS_TOKEN", "").strip()


def _auth_headers() -> dict[str, str]:
    token = _agent_bus_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _run_check_subprocess() -> tuple[dict[str, Any], int]:
    """Run the same read-only check as ``make skill-graph-check``."""
    repo = _repo_root()
    script = repo / "scripts/cortex/ingest_skills.py"
    if not script.is_file():
        raise FileNotFoundError(f"missing ingest_skills entrypoint: {script}")
    proc = subprocess.run(
        [sys.executable, str(script), "--check", "--report"],
        capture_output=True,
        text=True,
        cwd=str(repo),
        env=os.environ.copy(),
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise RuntimeError(
            f"ingest_skills --check --report produced no stdout "
            f"(exit={proc.returncode}, stderr={proc.stderr[:300]!r})"
        )
    report = json.loads(stdout.splitlines()[-1])
    return report, proc.returncode


def _should_alert(report: dict[str, Any]) -> bool:
    drift_count = int(report.get("drift_count") or 0)
    if drift_count <= 0:
        return False
    if drift_count > _EDGE_THRESHOLD:
        return True
    return _state.consecutive_dirty_runs >= _RUN_THRESHOLD


def _compose_alert_body(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "[skill-graph-drift]",
            "",
            f"**drift_count**: {report.get('drift_count')}",
            f"**stale_edges**: {report.get('stale_edges')}",
            f"**missing_edges**: {report.get('missing_edges')}",
            f"**consecutive_dirty_runs**: {_state.consecutive_dirty_runs}",
            f"**last_clean_ts**: {_state.last_clean_ts or report.get('last_clean_ts')}",
            "",
            "Read-only monitor — repair with `make skill-graph-reconcile`.",
        ]
    )


def _post_bus_alert(report: dict[str, Any]) -> None:
    token = _agent_bus_token()
    if not token:
        logger.warning(
            "skill-graph drift alert skipped: AGENT_BUS_TOKEN not configured"
        )
        return
    subject = (
        f"Skill graph drift alert: {report.get('drift_count')} drift(s) "
        f"(runs={_state.consecutive_dirty_runs})"
    )
    payload = {
        "thread": _ALERT_THREAD,
        "from": _ALERT_FROM,
        "to": "all",
        "subject": subject,
        "body": _compose_alert_body(report),
    }
    with make_sync_client(_AGENT_BUS_URL, timeout=10.0) as client:
        resp = client.post("/turns", json=payload, headers=_auth_headers())
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"agent-bus alert post failed: HTTP {resp.status_code} {resp.text[:300]}"
            )


def _apply_hysteresis(report: dict[str, Any]) -> None:
    clean = bool(report.get("clean"))
    if clean:
        _state.consecutive_dirty_runs = 0
        _state.last_clean_ts = report.get("last_clean_ts") or datetime.now(
            UTC
        ).isoformat()
        _state.alert_active = False
        return

    _state.consecutive_dirty_runs += 1
    if not _should_alert(report):
        return
    if _state.alert_active:
        return
    _post_bus_alert(report)
    _state.alert_active = True
    cortex_skill_graph_drift_alert(
        drift_count=int(report.get("drift_count") or 0),
        stale_edges=int(report.get("stale_edges") or 0),
        missing_edges=int(report.get("missing_edges") or 0),
        consecutive_dirty_runs=_state.consecutive_dirty_runs,
        thread=_ALERT_THREAD,
    )


def _sweep() -> None:
    report, exit_code = _run_check_subprocess()
    last_clean = _state.last_clean_ts or report.get("last_clean_ts")
    cortex_skill_graph_drift_checked(
        drift_count=int(report.get("drift_count") or 0),
        stale_edges=int(report.get("stale_edges") or 0),
        missing_edges=int(report.get("missing_edges") or 0),
        last_clean_ts=last_clean,
        clean=bool(report.get("clean")),
        exit_code=exit_code,
        consecutive_dirty_runs=_state.consecutive_dirty_runs,
    )
    _apply_hysteresis(report)


async def run_skill_graph_drift_monitor() -> None:
    """Periodic drift check loop. Runs until the hosting task is cancelled."""
    while True:
        await asyncio.sleep(_SWEEP_INTERVAL)
        try:
            await asyncio.to_thread(_sweep)
        except Exception as exc:
            logger.warning("skill-graph drift sweep failed", exc_info=True)
            cortex_skill_graph_drift_sweep_failed(error=str(exc)[:500])
