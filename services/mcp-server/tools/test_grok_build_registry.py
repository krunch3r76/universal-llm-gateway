"""In-flight cwd registry tests — acquire/release semantics, cwds_under,
disk persistence, and startup recovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tools._grok_build_registry import (
    SCHEMA_VERSION,
    _load_registry_from_disk,
    _pid_running,
    _reset_for_tests,
    cwds_under,
    get_dispatch_id,
    release_cwd,
    try_acquire_cwd,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    _reset_for_tests()


# ---------------------------------------------------------------------------
# Acquire / release semantics (Phase 1 baseline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_then_release_allows_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", tmp_path / "r.json")
    assert await try_acquire_cwd("/tmp") is True
    await release_cwd("/tmp")
    assert await try_acquire_cwd("/tmp") is True


@pytest.mark.asyncio
async def test_second_acquire_rejects_while_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", tmp_path / "r.json")
    assert await try_acquire_cwd("/tmp") is True
    assert await try_acquire_cwd("/tmp") is False
    await release_cwd("/tmp")


@pytest.mark.asyncio
async def test_acquire_distinct_cwds_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", tmp_path / "r.json")
    assert await try_acquire_cwd("/tmp") is True
    assert await try_acquire_cwd("/var") is True
    await release_cwd("/tmp")
    await release_cwd("/var")


@pytest.mark.asyncio
async def test_release_idempotent_on_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", tmp_path / "r.json")
    await release_cwd("/nonexistent")
    assert await try_acquire_cwd("/nonexistent") is True


@pytest.mark.asyncio
async def test_trailing_slash_canonicalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/tmp and /tmp/ collapse to the same key via realpath."""
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", tmp_path / "r.json")
    assert await try_acquire_cwd("/tmp") is True
    assert await try_acquire_cwd("/tmp/") is False
    await release_cwd("/tmp/")


@pytest.mark.asyncio
async def test_cwds_under_exact_match(
    tmp_path: os.PathLike[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "tools._grok_build_registry.REGISTRY_PATH", Path(str(tmp_path)) / "r.json"
    )
    root = str(tmp_path)
    assert await try_acquire_cwd(root) is True
    found = await cwds_under(root)
    assert os.path.realpath(root) in found
    await release_cwd(root)


@pytest.mark.asyncio
async def test_cwds_under_nested_match(
    tmp_path: os.PathLike[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cwd inside a worktree prefix is included by cwds_under."""
    monkeypatch.setattr(
        "tools._grok_build_registry.REGISTRY_PATH", Path(str(tmp_path)) / "r.json"
    )
    root = str(tmp_path)
    nested = os.path.join(root, "inside")
    os.makedirs(nested)
    assert await try_acquire_cwd(nested) is True
    found = await cwds_under(root)
    assert os.path.realpath(nested) in found
    await release_cwd(nested)


@pytest.mark.asyncio
async def test_cwds_under_no_partial_name_match(
    tmp_path: os.PathLike[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """/a/foo must not match prefix /a/foo-bar."""
    monkeypatch.setattr(
        "tools._grok_build_registry.REGISTRY_PATH", Path(str(tmp_path)) / "r.json"
    )
    base = str(tmp_path)
    sibling = os.path.join(base, "foo")
    cousin = os.path.join(base, "foo-bar")
    os.makedirs(sibling)
    os.makedirs(cousin)
    assert await try_acquire_cwd(sibling) is True
    found = await cwds_under(cousin)
    assert found == {}
    await release_cwd(sibling)


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_writes_cwd_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After try_acquire_cwd, the registry file contains the cwd."""
    reg = tmp_path / "registry.json"
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", reg)

    cwd = str(tmp_path / "repo")
    os.makedirs(cwd)
    assert await try_acquire_cwd(cwd, "dispatch-abc") is True

    data: dict[str, Any] = json.loads(reg.read_text())
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["writer_pid"] == os.getpid()
    cwds = [e["cwd"] for e in data["entries"]]
    assert os.path.realpath(cwd) in cwds
    matched = next(e for e in data["entries"] if e["cwd"] == os.path.realpath(cwd))
    assert matched["dispatch_id"] == "dispatch-abc"


@pytest.mark.asyncio
async def test_release_removes_cwd_from_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After release_cwd, the cwd is absent from the registry file."""
    reg = tmp_path / "registry.json"
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", reg)

    cwd = str(tmp_path / "repo")
    os.makedirs(cwd)
    await try_acquire_cwd(cwd)
    await release_cwd(cwd)

    data: dict[str, Any] = json.loads(reg.read_text())
    assert data["entries"] == []


@pytest.mark.asyncio
async def test_registry_write_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No temp file left behind after a successful write."""
    reg = tmp_path / "registry.json"
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", reg)

    await try_acquire_cwd("/tmp")
    leftover = [
        f for f in tmp_path.iterdir() if f.name.startswith(".grok_build_registry_")
    ]
    assert leftover == []


# ---------------------------------------------------------------------------
# Startup recovery — _load_registry_from_disk
# ---------------------------------------------------------------------------


def _write_fake_registry(path: Path, *, pid: int, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "writer_pid": pid,
                "entries": entries,
            }
        )
    )


def test_startup_prunes_stale_entries_when_pid_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entries owned by a dead PID are pruned on startup; _in_flight stays empty."""
    reg = tmp_path / "registry.json"
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", reg)

    # Use a PID guaranteed not to be running (max+1 wraps; use a known-dead value).
    dead_pid = 99999999
    with patch("tools._grok_build_registry._pid_running", return_value=False):
        _write_fake_registry(reg, pid=dead_pid, entries=["/some/cwd"])
        _reset_for_tests()  # clear any state from the module-level init call

        events: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            "tools._grok_build_events.record",
            lambda sig, **kw: events.append((sig, kw)),
        )
        _load_registry_from_disk()

    import tools._grok_build_registry as reg_mod

    assert reg_mod._in_flight == {}
    assert any(sig == "mcp.grok.build.registry.recovered" for sig, _ in events)
    evt_payload = next(
        kw for sig, kw in events if sig == "mcp.grok.build.registry.recovered"
    )
    assert evt_payload["entries_recovered"] == 0
    assert evt_payload["entries_pruned"] == 1


def test_startup_recovers_entries_when_pid_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entries owned by a running PID are loaded into _in_flight."""
    reg = tmp_path / "registry.json"
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", reg)

    with patch("tools._grok_build_registry._pid_running", return_value=True):
        _write_fake_registry(reg, pid=os.getpid(), entries=["/live/cwd"])
        _reset_for_tests()

        _load_registry_from_disk()

    import tools._grok_build_registry as reg_mod

    assert (
        os.path.realpath("/live/cwd") in reg_mod._in_flight
        or "/live/cwd" in reg_mod._in_flight
    )


def test_startup_emits_recovery_event_with_correct_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery event payload matches pruned/recovered counts."""
    reg = tmp_path / "registry.json"
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", reg)

    with patch("tools._grok_build_registry._pid_running", return_value=False):
        _write_fake_registry(reg, pid=1, entries=["/a", "/b", "/c"])
        _reset_for_tests()

        events: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            "tools._grok_build_events.record",
            lambda sig, **kw: events.append((sig, kw)),
        )
        _load_registry_from_disk()

    evt = next(kw for sig, kw in events if sig == "mcp.grok.build.registry.recovered")
    assert evt["entries_pruned"] == 3
    assert evt["entries_recovered"] == 0
    assert evt["schema_version"] == SCHEMA_VERSION


def test_startup_no_file_emits_zero_zero_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no registry file exists, recovery event fires with 0/0."""
    reg = tmp_path / "nonexistent.json"
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", reg)
    _reset_for_tests()

    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "tools._grok_build_events.record", lambda sig, **kw: events.append((sig, kw))
    )
    _load_registry_from_disk()

    evt = next(kw for sig, kw in events if sig == "mcp.grok.build.registry.recovered")
    assert evt["entries_recovered"] == 0
    assert evt["entries_pruned"] == 0


# ---------------------------------------------------------------------------
# _pid_running
# ---------------------------------------------------------------------------


def test_pid_running_current_process() -> None:
    assert _pid_running(os.getpid()) is True


def test_pid_running_dead_pid() -> None:
    # PID 0 is not a valid user process and os.kill(0, 0) has special semantics
    # (sends to process group). Use a known-impossible large PID instead.
    assert _pid_running(99999999) is False


# ---------------------------------------------------------------------------
# dispatch_id exposure (thread 1028 followup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dispatch_id_returns_recorded_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", tmp_path / "r.json")
    assert await try_acquire_cwd("/tmp", "uuid-xyz") is True
    assert await get_dispatch_id("/tmp") == "uuid-xyz"
    await release_cwd("/tmp")
    assert await get_dispatch_id("/tmp") is None


@pytest.mark.asyncio
async def test_get_dispatch_id_none_for_empty_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy fixtures may pre-populate without a uuid; surface as None."""
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", tmp_path / "r.json")
    assert await try_acquire_cwd("/tmp") is True
    assert await get_dispatch_id("/tmp") is None
    await release_cwd("/tmp")


@pytest.mark.asyncio
async def test_cwds_under_carries_dispatch_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", tmp_path / "r.json")
    root = str(tmp_path)
    nested = os.path.join(root, "inside")
    os.makedirs(nested)
    assert await try_acquire_cwd(nested, "did-1") is True
    found = await cwds_under(root)
    assert found[os.path.realpath(nested)] == "did-1"
    await release_cwd(nested)


def test_legacy_v1_string_entries_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A v1 registry file (bare cwd strings) loads with empty dispatch_id."""
    reg = tmp_path / "registry.json"
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", reg)
    reg.write_text(
        json.dumps(
            {"schema_version": 1, "writer_pid": os.getpid(), "entries": ["/legacy/cwd"]}
        )
    )
    with patch("tools._grok_build_registry._pid_running", return_value=True):
        _reset_for_tests()
        _load_registry_from_disk()
    import tools._grok_build_registry as reg_mod

    assert reg_mod._in_flight.get("/legacy/cwd") == ""
