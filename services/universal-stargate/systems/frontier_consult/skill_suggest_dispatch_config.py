"""Load skill-suggest dispatch orchestration timeouts from pipeline YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx
import yaml

_ULG_REPO_DIRNAME = "universal-llm-gateway"
_CONFIG_REL = Path("pipelines/skill_suggest_rank/v1/skill-suggest-rank.yaml")


@dataclass(frozen=True, slots=True)
class SkillSuggestDispatchConfig:
    idle_timeout_seconds: float
    ack_window_seconds: float
    idle_poll_interval_seconds: float
    cortex_timeout_seconds: float
    mcp_relay_timeout_seconds: float
    agent_bus_wait_chunk_seconds: float
    agent_bus_client_timeout_seconds: float
    agent_bus_max_wait_seconds: float
    worker_probe_timeout_seconds: float
    worker_dispatch_http_timeout_seconds: float
    wait_retry_backoff_seconds: float
    worker_outer_timeout_seconds: float

    @property
    def worker_dispatch_http_timeout(self) -> httpx.Timeout:
        read_s = self.worker_dispatch_http_timeout_seconds
        return httpx.Timeout(connect=5.0, read=read_s, write=read_s, pool=5.0)


def _repo_root() -> Path:
    root = Path(os.environ.get("PROJECT_ROOT") or "/mnt/torus/projects").expanduser()
    if (root / "pipelines/skill_suggest_rank").is_dir():
        return root
    nested = root / _ULG_REPO_DIRNAME
    if (nested / "pipelines/skill_suggest_rank").is_dir():
        return nested
    return root


def _config_path() -> Path:
    return _repo_root() / _CONFIG_REL


def _require_section(raw: dict, key: str) -> dict:
    section = raw.get(key)
    if not isinstance(section, dict):
        raise ValueError(f"skill-suggest-rank.yaml missing required section: {key!r}")
    return section


def _require_float(section: dict, key: str) -> float:
    if key not in section:
        raise ValueError(f"dispatch config missing required key: {key!r}")
    return float(section[key])


@lru_cache(maxsize=1)
def load_skill_suggest_dispatch_config() -> SkillSuggestDispatchConfig:
    path = _config_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid pipeline yaml: {path}")
    dispatch = _require_section(data, "dispatch")
    idle = _require_section(dispatch, "idle")
    liveness = _require_section(dispatch, "liveness")
    cortex = _require_section(dispatch, "cortex")
    transport = _require_section(dispatch, "transport")
    worker = _require_section(dispatch, "worker")
    return SkillSuggestDispatchConfig(
        idle_timeout_seconds=_require_float(idle, "timeout_seconds"),
        ack_window_seconds=_require_float(liveness, "ack_window_seconds"),
        idle_poll_interval_seconds=_require_float(liveness, "idle_poll_interval_seconds"),
        cortex_timeout_seconds=_require_float(cortex, "timeout_seconds"),
        mcp_relay_timeout_seconds=_require_float(
            transport, "mcp_relay_timeout_seconds"
        ),
        agent_bus_wait_chunk_seconds=_require_float(
            transport, "agent_bus_wait_chunk_seconds"
        ),
        agent_bus_client_timeout_seconds=_require_float(
            transport, "agent_bus_client_timeout_seconds"
        ),
        agent_bus_max_wait_seconds=_require_float(
            transport, "agent_bus_max_wait_seconds"
        ),
        worker_probe_timeout_seconds=_require_float(
            transport, "worker_probe_timeout_seconds"
        ),
        worker_dispatch_http_timeout_seconds=_require_float(
            transport, "worker_dispatch_http_timeout_seconds"
        ),
        wait_retry_backoff_seconds=_require_float(
            transport, "wait_retry_backoff_seconds"
        ),
        worker_outer_timeout_seconds=_require_float(worker, "outer_timeout_seconds"),
    )


def reset_skill_suggest_dispatch_config_cache() -> None:
    load_skill_suggest_dispatch_config.cache_clear()
