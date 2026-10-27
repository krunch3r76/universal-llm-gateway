"""Hermetic isolation for offline mechanical-land (stage-4) pytest modules."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from services.git_integration_worker.mechanical_land.reactor import ReactorDeps
from services.git_integration_worker.mechanical_land.store import MechanicalLandStore


def _is_mechanical_land_offline_test(nodeid: str) -> bool:
    return (
        "test_mechanical_land_reactor.py" in nodeid
        or "test_mechanical_land_rulings.py" in nodeid
    )


def offline_bus_post(**kwargs: object) -> int:
    return 1


def offline_post_admit(_body: dict) -> tuple[int, dict]:
    return 503, {"error": "offline_test_stub"}


def empty_get_turns(**_kwargs: object) -> list[dict[str, Any]]:
    return []


def reactor_deps(
    store: MechanicalLandStore,
    fetch_plan_fn: object,
    *,
    post_admit_fn: object | None = None,
    bus_post_fn: Callable[..., int | None] | None = None,
    get_turns_fn: Callable[..., list[dict[str, Any]]] | None = None,
    **extra: object,
) -> ReactorDeps:
    return ReactorDeps(
        store=store,
        fetch_plan_fn=fetch_plan_fn,
        post_admit_fn=post_admit_fn or offline_post_admit,
        bus_post_fn=bus_post_fn or offline_bus_post,
        get_turns_fn=get_turns_fn or empty_get_turns,
        **extra,
    )


@pytest.fixture
def mechanical_land_bus_post_calls() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def mechanical_land_admit_post_calls() -> list[dict[str, Any]]:
    return []


@pytest.fixture(autouse=True)
def _mechanical_land_offline_isolation(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mechanical_land_bus_post_calls: list[dict[str, Any]],
    mechanical_land_admit_post_calls: list[dict[str, Any]],
) -> None:
    if not _is_mechanical_land_offline_test(request.node.nodeid):
        return

    bus_db = tmp_path / "agent-bus-isolated.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(bus_db))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "1")

    def _recording_bus_post(**kwargs: object) -> int:
        mechanical_land_bus_post_calls.append(dict(kwargs))
        return len(mechanical_land_bus_post_calls)

    def _recording_post_admit(body: dict) -> tuple[int, dict]:
        mechanical_land_admit_post_calls.append(dict(body))
        return 503, {"error": "offline_test_stub"}

    monkeypatch.setattr(
        "services.git_integration_worker.tests.mechanical_land_isolation.offline_bus_post",
        _recording_bus_post,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.tests.mechanical_land_isolation.offline_post_admit",
        _recording_post_admit,
    )

    def _forbidden_create_turn(*_a: object, **_k: object) -> None:
        raise AssertionError(
            "agent_bus create_turn invoked from offline mechanical_land test"
        )

    for target in (
        "services.git_integration_worker.mechanical_land.blocked.create_turn",
        "services.git_integration_worker.mechanical_land.rulings.create_turn",
    ):
        monkeypatch.setattr(target, _forbidden_create_turn)

    def _stargate_stub(_body: dict) -> tuple[int, dict]:
        return 503, {"error": "offline_test_stub"}

    monkeypatch.setattr(
        "services.git_integration_worker.mechanical_land.store.post_stargate_team_dispatch",
        _stargate_stub,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.mechanical_land.reactor.post_stargate_team_dispatch",
        _stargate_stub,
    )

    _orig_request = httpx.Client.request

    def _guard_request(
        self: httpx.Client,
        method: str,
        url: str | httpx.URL,
        **kwargs: object,
    ) -> httpx.Response:
        url_l = str(url).lower()
        if any(
            token in url_l
            for token in (
                "agent-bus",
                "stargate",
                ":8765",
                "127.0.0.1:8765",
                "/tmp/universal-protocol/agent-bus",
            )
        ):
            raise AssertionError(
                f"offline mechanical_land test attempted live httpx to {url!r}"
            )
        return _orig_request(self, method, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "request", _guard_request)
