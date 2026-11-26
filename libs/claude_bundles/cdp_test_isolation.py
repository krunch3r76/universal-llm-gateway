"""G1 registry isolation fixture — redirects live roots into tmp_path."""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

G1_TEST_PORT_RANGE = range(19100, 19110)

# --- hunk: assert_g1_ports_free ---
def assert_g1_ports_free() -> None:
    """Refuse when any G1 test port is already bound."""
    for port in G1_TEST_PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.05)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise pytest.skip(f"G1 port {port} already bound; isolation refused")


@dataclass
class G1IsolationContext:
    tmp_path: Path
    reclaim_trash: Path
    registry_root: Path
    ledger: list[dict[str, Any]]
    port_range: range
    note_test_pid: Callable[[int], None]
    pid_alive: Callable[[int], bool]
    install_token: str

    def spawn(self, argv: list[str]) -> int:
        import g1_live_surface_guard as g

        return g.spawn(argv)


def apply_g1_registry_isolation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    guard: Any | None = None,
    **_opt: object,
) -> G1IsolationContext:
    """Layer-1 isolation seam; optional injected guard from the pytest plugin."""
    if guard is None:
        from claude_bundles import g1_live_surface_guard as guard_mod

        guard = guard_mod

    assert_g1_ports_free()

    from claude_bundles import cdp_registry as reg
    from claude_bundles import cdp_lane
    from claude_bundles.cdp_registry import hygiene

    registry_root = tmp_path / "cdp-registry"
    registry_root.mkdir(parents=True, exist_ok=True)
    regs = registry_root / "registrations"
    regs.mkdir(parents=True, exist_ok=True)
    profiles = tmp_path / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    reclaim_trash = tmp_path / "reclaim-trash"
    # reclaim trash is created by production at hygiene.py:66-67, not here
    lane_dir = tmp_path / "cdp-lanes"
    lane_dir.mkdir(parents=True, exist_ok=True)
    events_sock = tmp_path / "events.sock"

    # --- hunk: EVENTS_INGEST_SOCK ---
    monkeypatch.setenv("EVENTS_INGEST_SOCK", str(events_sock))
    monkeypatch.setenv("EVENTS_INGEST_TCP", "")
    # --- hunk: ULG_BROWSER_ADMISSION_PREFLIGHT ---
    monkeypatch.setenv("ULG_BROWSER_ADMISSION_PREFLIGHT", "off")
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")

    # --- hunk: reg.RECLAIM_TRASH_DIR ---
    monkeypatch.setattr(reg, "RECLAIM_TRASH_DIR", reclaim_trash)

    # --- hunk: reg._store + cdp_lane.LANE_DIR ---
    store = reg._store
    for name, value in {
        "REGISTRY_DIR": registry_root,
        "REGISTRY_LOG": registry_root / "registry.jsonl",
        "ACTIVE_JSON": registry_root / "active.json",
        "SESSIONS_JSON": registry_root / "sessions.json",
        "SESSION_TRANSITIONS_JSONL": registry_root / "session_transitions.jsonl",
        "PORTS_LOCK": registry_root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }.items():
        monkeypatch.setattr(store, name, value)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(cdp_lane, "LANE_DIR", lane_dir)
    monkeypatch.setattr(cdp_lane, "ALLOC_LOCK", lane_dir / "alloc.lock")

    # --- hunk: reg.PORT_RANGE ---
    monkeypatch.setattr(reg, "PORT_RANGE", G1_TEST_PORT_RANGE)

    # --- hunk: cdp_lane.is_listening ---
    monkeypatch.setattr(cdp_lane, "is_listening", lambda _p: False)

    # --- hunk: cdp_lane.chrome_port_for_profile ---
    monkeypatch.setattr(cdp_lane, "chrome_port_for_profile", lambda _p: None)

    # --- hunk: hygiene._pid_alive ---
    monkeypatch.setattr(hygiene, "_pid_alive", lambda _pid: False)

    # --- hunk: reg._kill_listener ---
    recorded: list[int] = []

    def _recorder(port: int) -> None:
        recorded.append(port)

    monkeypatch.setattr(reg, "_kill_listener", _recorder)

    monkeypatch.setattr(
        cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    monkeypatch.setattr(cdp_lane, "PRIMARY_PROFILE", profiles / "primary")
    (profiles / "primary").mkdir(parents=True, exist_ok=True)

    return G1IsolationContext(
        tmp_path=tmp_path,
        reclaim_trash=reclaim_trash,
        registry_root=registry_root,
        ledger=guard.ledger(),
        port_range=G1_TEST_PORT_RANGE,
        note_test_pid=guard.note_test_pid,
        pid_alive=lambda pid: hygiene._pid_alive(pid),
        install_token=guard.install_token(),
    )