"""Guard tests for GIW work-registry relay isolation in offline Stargate pytest runs.

Covers dead-port env wiring, relay swallow on connection refusal, and ``live_giw`` opt-out.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

import systems.frontier_consult.cursor_sdk_lane_subscription as lane_subscription

from systems.frontier_consult.cursor_sdk_worker_dispatch import worker_base_url
from systems.frontier_consult.work_registry_admit_relay import relay_registry_record


def test_worker_base_url_uses_dead_port_not_default_8091(giw_dead_port: int) -> None:
    """``worker_base_url`` must follow the autouse dead-port env, not live 8091."""
    url = worker_base_url()
    assert url == f"http://127.0.0.1:{giw_dead_port}"
    assert url != "http://127.0.0.1:8091"


@pytest.mark.asyncio
async def test_relay_registry_record_returns_none_against_dead_port(
    giw_dead_port: int,
) -> None:
    """Unreachable GIW must swallow errors and avoid live registry writes."""
    captured_base: list[str] = []

    def _capture_client(base_url: str, **kwargs: Any) -> Any:
        captured_base.append(base_url)
        from transport_utils import make_async_client as real_make

        return real_make(base_url, **kwargs)

    with patch(
        "systems.frontier_consult.work_registry_admit_relay.make_async_client",
        side_effect=_capture_client,
    ):
        body = await relay_registry_record(
            request_id="req-isolation",
            dispatch_id="disp-isolation",
            thread_id="21841",
            work_key="friction:test-isolation",
        )

    assert body is None
    assert len(captured_base) == 1
    assert captured_base[0] == f"http://127.0.0.1:{giw_dead_port}"


@pytest.mark.live_giw
def test_live_giw_marker_skips_dead_port_env(giw_dead_port: int) -> None:
    """``live_giw`` tests keep operator env instead of the session dead port."""
    assert os.environ.get("GIT_INTEGRATION_WORKER_PORT") != str(giw_dead_port)
    url = os.environ.get("GIT_INTEGRATION_WORKER_URL")
    if url is not None:
        assert str(giw_dead_port) not in url
    assert str(giw_dead_port) not in lane_subscription._GIW


def test_offline_giw_isolation_patches_modules_before_dead_port_setenv() -> None:
    """Regression: patch-after-setenv left import-time URLs on the dead port."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "conftest.py"
    body = source.read_text(encoding="utf-8")
    marker = '_patch_import_time_giw_constants(monkeypatch, dead_url, giw_dead_port)'
    setenv = 'monkeypatch.setenv("GIT_INTEGRATION_WORKER_HOST"'
    assert body.index(marker) < body.index(setenv)


def test_wrong_giw_isolation_order_leaves_dead_import_time_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch-after-setenv + cleared env leaves ``_GIW`` on dead URL (live_giw hazard)."""
    from conftest import _patch_import_time_giw_constants

    dead_port = 58881
    dead_url = f"http://127.0.0.1:{dead_port}"
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", dead_url)
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_PORT", str(dead_port))
    _patch_import_time_giw_constants(monkeypatch, dead_url, dead_port)
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_URL", raising=False)
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_HOST", raising=False)
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_PORT", raising=False)
    assert lane_subscription._GIW == dead_url
