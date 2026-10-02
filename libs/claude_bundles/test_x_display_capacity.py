"""X display capacity probe — mint refuse, log scrape, unix-table parse."""

from __future__ import annotations

from pathlib import Path

import pytest
from admission_common.qualified_scalar import SurfaceDecl

from claude_bundles.x_display_capacity import (
    _X_MAX_SCOPE,
    XDisplayCapacityError,
    attach_x_display_capacity,
    count_x11_unix_clients,
    display_x11_socket_name,
    exhausted_message,
    listen_timeout_display_dead_message,
    listen_timeout_x_message,
    log_bytes_show_display_dead,
    log_bytes_show_x_exhaustion,
    probe_x_display,
    require_cdp_display_reachable,
    require_chrome_headroom,
    x_display_wire_fields,
)

pytestmark = [pytest.mark.offline, pytest.mark.live_x_display]


def test_display_socket_name_strips_screen() -> None:
    assert display_x11_socket_name(":2") == "X2"
    assert display_x11_socket_name(":2.0") == "X2"
    assert display_x11_socket_name("2") == "X2"


def test_display_socket_name_rejects_garbage() -> None:
    from claude_bundles.cdp_display_auth import DisplayAuthError

    with pytest.raises(DisplayAuthError):
        display_x11_socket_name("")
    with pytest.raises(DisplayAuthError):
        display_x11_socket_name(":")


def test_count_x11_unix_clients_from_table(tmp_path: Path) -> None:
    table = tmp_path / "unix"
    table.write_text(
        "Num RefCount Protocol Flags Type St Inode Path\n"
        "00000000: 00000002 00000000 00000000 0001 01 1 /tmp/.X11-unix/X2\n"
        "00000000: 00000003 00000000 00000000 0001 01 2 /tmp/.X11-unix/X2\n"
        "00000000: 00000003 00000000 00000000 0001 01 3 @/tmp/.X11-unix/X1\n",
        encoding="utf-8",
    )
    assert count_x11_unix_clients(":2", proc_net_unix=table) == 2


def test_count_unreadable_is_none(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert count_x11_unix_clients(":2", proc_net_unix=missing) is None


def test_probe_unobserved_when_proc_missing(tmp_path: Path) -> None:
    snap = probe_x_display(
        display=":2",
        max_clients=64,
        chrome_budget=8,
        proc_net_unix=tmp_path / "missing",
    )
    assert snap["x_clients"] is None
    assert snap["x_exhausted"] is None
    assert snap["x_headroom"] is None
    assert snap["x_probe"] == "unavailable"


def test_probe_exhausted_at_63_of_64() -> None:
    snap = probe_x_display(display=":2", count=63, max_clients=64, chrome_budget=8)
    assert snap["x_clients"] == 63
    assert snap["x_headroom"] == 1
    assert snap["x_exhausted"] is True
    assert snap["x_probe"] == "injected"


def test_probe_allows_when_headroom_meets_budget() -> None:
    snap = probe_x_display(display=":2", count=56, max_clients=64, chrome_budget=8)
    assert snap["x_headroom"] == 8
    assert snap["x_exhausted"] is False


def test_require_raises_named_x_error_not_chrome_timeout() -> None:
    with pytest.raises(XDisplayCapacityError, match="X display :2 exhausted") as caught:
        require_chrome_headroom(display=":2", count=63, max_clients=64, chrome_budget=8)
    assert "Chrome CDP" in str(caught.value)
    assert "did not reach CDP in" not in str(caught.value)


def test_require_exhausted_emits_after_drain_false_without_drain_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_bundles import cdp_registry_events as ev

    captured: list[object] = []
    monkeypatch.setattr(ev, "emit", lambda event: captured.append(event))

    with pytest.raises(XDisplayCapacityError):
        require_chrome_headroom(display=":2", count=63, max_clients=64, chrome_budget=8)

    assert len(captured) == 1
    event = captured[0]
    assert getattr(event, "signal") == "cdp.display.exhausted"
    assert event.payload["after_drain"] is False  # type: ignore[attr-defined]


def test_require_passes_when_unobserved(tmp_path: Path) -> None:
    snap = require_chrome_headroom(display=":2", proc_net_unix=tmp_path / "missing")
    assert snap["x_exhausted"] is None


def test_log_scrape_ignores_bytes_before_this_launch(tmp_path: Path) -> None:
    log = tmp_path / "chrome.log"
    prior = b"Maximum number of clients reached\n"
    log.write_bytes(prior + b"this launch: missing display\n")
    assert log_bytes_show_x_exhaustion(str(log), start_offset=0) is True
    assert log_bytes_show_x_exhaustion(str(log), start_offset=len(prior)) is False


def test_log_scrape_detects_this_launch_token(tmp_path: Path) -> None:
    log = tmp_path / "chrome.log"
    prior = b"old noise\n"
    log.write_bytes(prior + b"Maximum number of clients reached\n")
    assert log_bytes_show_x_exhaustion(str(log), start_offset=len(prior)) is True


def test_listen_timeout_message_names_x() -> None:
    msg = listen_timeout_x_message(9235, "/tmp/chrome-cdp-claude-ai-9235.log")
    assert "9235" in msg
    assert "Maximum number of clients reached" in msg
    assert "not a browser hang" in msg


def test_log_scrape_detects_display_dead_this_launch(tmp_path: Path) -> None:
    log = tmp_path / "chrome.log"
    prior = b"Authorization required, but no authorization protocol specified\n"
    log.write_bytes(prior + b"Missing X server or $DISPLAY\n")
    assert log_bytes_show_display_dead(str(log), start_offset=0) is True
    assert log_bytes_show_display_dead(str(log), start_offset=len(prior)) is True
    assert log_bytes_show_display_dead(str(log), start_offset=len(prior) + 50) is False


def test_listen_timeout_display_dead_message_names_auth_path() -> None:
    msg = listen_timeout_display_dead_message(
        9225, "/tmp/chrome-cdp-claude-ai-9225.log", ":2"
    )
    assert "9225" in msg
    assert ":2" in msg
    assert "cdp-xvfb" in msg


def test_require_cdp_display_reachable_missing_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CDP_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        "claude_bundles.cdp_lane.Path.home", staticmethod(lambda: tmp_path)
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_display_auth.Path.home", staticmethod(lambda: tmp_path)
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_display_auth.discover_live_xvfb_auth",
        lambda _d: None,
    )
    with pytest.raises(XDisplayCapacityError, match="no resolvable Xauthority"):
        require_cdp_display_reachable()


def test_exhausted_message_shape() -> None:
    msg = exhausted_message(
        {
            "x_display": ":2",
            "x_clients": 63,
            "x_max_clients": 64,
            "x_chrome_client_budget": 8,
        }
    )
    assert msg.startswith("X display :2 exhausted: 63 of 64")


def test_wire_fields_qualify_numerics() -> None:
    fields = x_display_wire_fields(
        probe_x_display(display=":2", count=63, max_clients=64, chrome_budget=8)
    )
    assert fields["x_clients"] == 63
    assert fields["x_clients_authority"] == "observed"
    assert fields["x_exhausted"] is True
    assert fields["x_max_clients_authority"] == "recorded"
    assert fields["x_display"] == ":2"


def test_max_clients_default_64_for_desktop_colon_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No live argv and no env → default 64, including desktop ``:1``."""
    monkeypatch.delenv("CDP_X_MAX_CLIENTS", raising=False)
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity._proc_cmdlines",
        lambda proc_root=None: [],
    )
    snap = probe_x_display(display=":1", count=10)
    assert snap["x_max_clients"] == 64


def test_live_maxclients_stale_ceiling_inside_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second call within TTL returns the stored ceiling, not the new cmdline."""
    import claude_bundles.x_display_capacity as xdc

    monkeypatch.setattr(xdc, "_MAXCLIENTS_CACHE", {})

    cmdline_snapshots: list[list[list[str]]] = [
        [["Xvfb", ":2", "-maxclients", "64"]],
        [["Xvfb", ":2", "-maxclients", "128"]],
    ]
    reader_calls = 0

    def reader(proc_root=None) -> list[list[str]]:
        nonlocal reader_calls
        snapshot = cmdline_snapshots[min(reader_calls, len(cmdline_snapshots) - 1)]
        reader_calls += 1
        return snapshot

    monkeypatch.setattr(xdc, "_proc_cmdlines", reader)

    t0 = 10_000.0
    monotonic_reads = iter([t0, t0 + 4.9])

    monkeypatch.setattr(xdc.time, "monotonic", lambda: next(monotonic_reads))

    assert xdc._live_maxclients(":2") == 64
    assert xdc._live_maxclients(":2") == 64
    assert reader_calls == 1


def test_max_clients_belief_is_min_of_live_argv_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env 256 cannot outvote a live Xvfb that was started at 64."""
    monkeypatch.setenv("CDP_X_MAX_CLIENTS", "256")
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity._proc_cmdlines",
        lambda proc_root=None: [["/usr/bin/Xvfb", ":2", "-maxclients", "64"]],
    )
    assert probe_x_display(display=":2", count=0)["x_max_clients"] == 64
    monkeypatch.setenv("CDP_X_MAX_CLIENTS", "32")
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity._proc_cmdlines",
        lambda proc_root=None: [["Xvfb", ":2", "-maxclients", "256"]],
    )
    assert probe_x_display(display=":2", count=0)["x_max_clients"] == 32


def test_max_clients_belief_uses_live_argv_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDP_X_MAX_CLIENTS", raising=False)
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity._proc_cmdlines",
        lambda proc_root=None: [["Xvfb", ":2", "-auth", "/tmp/x", "-maxclients", "256"]],
    )
    assert probe_x_display(display=":2", count=0)["x_max_clients"] == 256
    assert probe_x_display(display=":3", count=0)["x_max_clients"] == 64


def test_max_clients_belief_env_when_procfs_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDP_X_MAX_CLIENTS", "128")
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity._proc_cmdlines",
        lambda proc_root=None: None,
    )
    assert probe_x_display(display=":2", count=0)["x_max_clients"] == 128


def test_proc_cmdlines_unreadable_returns_none(tmp_path: Path) -> None:
    from claude_bundles.x_display_capacity import (
        _maxclients_in_cmdlines,
        _proc_cmdlines,
    )

    assert _proc_cmdlines(tmp_path / "missing-proc") is None
    assert _maxclients_in_cmdlines(None, ":2") is None


@pytest.mark.parametrize(
    ("cmdlines", "expected"),
    [
        (
            [["Xvfb", ":2", "-maxclients", "256"], ["Xvfb", ":2", "-maxclients", "64"]],
            64,
        ),
        (
            [["Xvfb", ":2", "-maxclients", "64"], ["Xvfb", ":2", "-maxclients", "256"]],
            64,
        ),
        (
            [["Xvfb", ":2"], ["Xvfb", ":2", "-maxclients", "128"]],
            128,
        ),
    ],
)
def test_maxclients_min_across_duplicate_xvfb_on_display(
    cmdlines: list[list[str]], expected: int
) -> None:
    from claude_bundles.x_display_capacity import _maxclients_in_cmdlines

    assert _maxclients_in_cmdlines(cmdlines, ":2") == expected


def test_wire_fields_x_max_scope_no_display_pin() -> None:
    fields = x_display_wire_fields(probe_x_display(display=":1", count=0))
    assert fields["x_max_clients_scope"] == _X_MAX_SCOPE
    assert ":1" not in _X_MAX_SCOPE
    assert "128" not in _X_MAX_SCOPE


def test_attach_x_display_decl_names_resolved_display_only() -> None:
    decl = SurfaceDecl("active_work_snapshot")
    payload: dict[str, object] = {}
    attach_x_display_capacity(payload, decl)
    assert decl._plain["x_display"] == str(payload["x_display"])
    assert "DISPLAY" not in decl._plain["x_display"]
    assert "mint" not in decl._plain["x_display"].lower()


def test_reserved_chromes_consumes_one_chrome_budget(tmp_path: Path) -> None:
    snap = probe_x_display(display=":2", count=52, max_clients=64, chrome_budget=8)
    with pytest.raises(XDisplayCapacityError, match="X display") as caught:
        require_chrome_headroom(
            display=":2",
            count=52,
            max_clients=64,
            chrome_budget=8,
            reserved_chromes=1,
        )
    assert str(caught.value) == exhausted_message(snap)
    allowed = require_chrome_headroom(
        display=":2",
        count=52,
        max_clients=64,
        chrome_budget=8,
        reserved_chromes=0,
    )
    assert allowed["x_exhausted"] is False
    unobserved = require_chrome_headroom(
        display=":2",
        proc_net_unix=tmp_path / "missing",
        reserved_chromes=1,
    )
    assert unobserved["x_exhausted"] is None


def test_launch_chrome_keeps_log_timeout_without_prelaunch_headroom(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_lane
    from claude_bundles.cdp_lane import LaneError

    log = tmp_path / "chrome.log"
    log.write_bytes(b"")
    monkeypatch.setattr(cdp_lane, "_LAUNCH_WAIT_S", 0.05)
    monkeypatch.setattr(cdp_lane, "_seed_profile", lambda _profile: None)
    monkeypatch.setattr(cdp_lane, "is_listening", lambda _port: False)
    monkeypatch.setattr(cdp_lane, "_kill_lane_chrome", lambda _pid: None)
    monkeypatch.setattr(
        cdp_lane, "chrome_display_env", lambda _display: {"DISPLAY": ":2"}
    )
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.chrome_cdp_log_path", lambda _port: str(log)
    )
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.require_cdp_display_reachable",
        lambda **_kwargs: ":2",
    )

    def refuse_headroom(**_kwargs: object) -> None:
        raise AssertionError("pre-launch headroom must not run inside _launch_chrome")

    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.require_chrome_headroom", refuse_headroom
    )

    class _Proc:
        pid = 42

    def popen_with(payload: bytes):
        def _popen(*_args: object, **kwargs: object) -> _Proc:
            stdout = kwargs.get("stdout")
            if hasattr(stdout, "write"):
                stdout.write(payload)
                stdout.flush()
            return _Proc()

        return _popen

    monkeypatch.setattr(
        cdp_lane.subprocess, "Popen", popen_with(b"Maximum number of clients reached\n")
    )
    with pytest.raises(
        XDisplayCapacityError, match="Maximum number of clients reached"
    ):
        cdp_lane._launch_chrome(9223, tmp_path / "profile")

    monkeypatch.setattr(
        cdp_lane.subprocess, "Popen", popen_with(b"browser still starting\n")
    )
    with pytest.raises(LaneError, match="0.05"):
        cdp_lane._launch_chrome(9224, tmp_path / "profile")


def test_allocate_port_for_profile_still_calls_headroom(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_lane

    called: list[dict[str, object]] = []

    def spy(**kwargs: object) -> dict[str, bool]:
        called.append(dict(kwargs))
        return {"x_exhausted": False}

    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.require_chrome_headroom", spy
    )
    monkeypatch.setattr(cdp_lane, "chrome_port_for_profile", lambda _profile: None)
    monkeypatch.setattr(cdp_lane, "held_ports", lambda: [])
    monkeypatch.setattr(cdp_lane, "select_free_port", lambda *_args, **_kwargs: 9333)
    monkeypatch.setattr(cdp_lane, "_launch_chrome", lambda _port, _profile: 7)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.used_ports_snapshot",
        lambda: set(),
        raising=False,
    )
    profile = tmp_path / "profile"
    profile.mkdir()
    port, reused = cdp_lane._allocate_port_for_profile("suf", profile, launch=True)
    assert called
    assert port == 9333
    assert reused is False


def test_admit_display_falls_over_and_launch_env_uses_admitted_display(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Injected {:2: 57, :3: 22} admits :3; Chrome env DISPLAY is that display."""
    from claude_bundles import cdp_lane
    from claude_bundles import cdp_registry_events as ev
    from claude_bundles.cdp_display_auth import DisplayAuth
    from claude_bundles.x_display_capacity import admit_display

    fallovers: list[object] = []

    def _capture(event: object) -> None:
        if getattr(event, "signal", None) == "cdp.port.display_fallover":
            fallovers.append(event)

    monkeypatch.setattr(ev, "emit", _capture)
    admitted = admit_display(
        [":2", ":3"],
        {},
        counts={":2": 57, ":3": 22},
        max_clients=64,
        chrome_budget=8,
        auth_resolves=lambda _display: True,
    )
    assert admitted == ":3"
    assert fallovers
    assert fallovers[0].payload["admitted"] == ":3"  # type: ignore[attr-defined]

    captured: dict[str, object] = {}

    class _Proc:
        pid = 7

    def _popen(*_args: object, **kwargs: object) -> _Proc:
        captured["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(cdp_lane, "_seed_profile", lambda _profile: None)
    monkeypatch.setattr(cdp_lane, "_seed_lane_session", lambda _port, _pid: None)
    monkeypatch.setattr(cdp_lane, "is_listening", lambda _port: True)
    monkeypatch.setattr(cdp_lane.subprocess, "Popen", _popen)
    monkeypatch.setattr(
        "claude_bundles.cdp_display_auth.resolve_display_auth",
        lambda display, **_kwargs: DisplayAuth(
            path=tmp_path / "Xauthority", source="per_display", required=True
        ),
    )
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.require_cdp_display_reachable",
        lambda **_kwargs: ":3",
    )
    profile = tmp_path / "profile"
    profile.mkdir()
    cdp_lane._launch_chrome(9223, profile, display=admitted)
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["DISPLAY"] == ":3"


def test_admit_display_names_every_exhausted_candidate() -> None:
    from claude_bundles.x_display_capacity import admit_display

    with pytest.raises(XDisplayCapacityError) as caught:
        admit_display(
            [":2", ":3"],
            {},
            counts={":2": 57, ":3": 60},
            max_clients=64,
            chrome_budget=8,
            auth_resolves=lambda _display: True,
        )
    message = str(caught.value)
    assert ":2" in message
    assert ":3" in message
    assert "57" in message
    assert "60" in message


def test_single_candidate_matches_cdp_display_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_bundles.cdp_lane import cdp_display, cdp_display_candidates
    from claude_bundles.x_display_capacity import admit_display

    monkeypatch.delenv("CDP_DISPLAYS", raising=False)
    monkeypatch.setenv("CDP_DISPLAY", ":2")
    assert cdp_display() == ":2"
    assert cdp_display_candidates() == [":2", ":3"]
    monkeypatch.setenv("CDP_DISPLAY", ":3")
    assert cdp_display_candidates() == [":3", ":2"]
    monkeypatch.setenv("CDP_DISPLAY", ":1")
    assert cdp_display_candidates() == [":1"]
    monkeypatch.setenv("CDP_DISPLAY", ":2")
    monkeypatch.setenv("CDP_DISPLAYS", ":2")
    assert cdp_display_candidates() == [":2"]
    admitted = admit_display(
        [":2"],
        {},
        counts={":2": 56},
        max_clients=64,
        chrome_budget=8,
        auth_resolves=lambda _display: True,
    )
    snap = require_chrome_headroom(
        display=cdp_display(), count=56, max_clients=64, chrome_budget=8
    )
    assert admitted == ":2"
    assert snap["x_display"] == admitted
    assert snap["x_exhausted"] is False


def test_admit_display_is_sticky_not_least_loaded() -> None:
    """:2 still has a full Chrome budget; do not jump to the emptier :3."""
    from claude_bundles.x_display_capacity import admit_display

    admitted = admit_display(
        [":2", ":3"],
        {},
        counts={":2": 56, ":3": 0},
        max_clients=64,
        chrome_budget=8,
        auth_resolves=lambda _display: True,
    )
    assert admitted == ":2"


def test_admit_display_reserves_one_budget_per_pin_on_that_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse iff ``headroom - pin_count * budget < budget``.

    Four ``:3`` pins at budget 8 consume 32. Headroom 40 leaves 8, and
    ``8 < 8`` is false, so the mint is admitted. Headroom 39 leaves 7 and
    is refused. A ``:2`` pin in the same table is not charged to ``:3``.
    """
    from claude_bundles.x_display_capacity import admit_display

    monkeypatch.setattr(
        "claude_bundles.x_display_capacity._load_pin_lanes",
        lambda: {
            "fleet": {"display": ":2", "port": 9222},
            "messages": {"display": ":3", "port": 9250},
            "ess": {"display": ":3", "port": 9260},
            "gopuff": {"display": ":3", "port": 9270},
            "calendar": {"display": ":3", "port": 9290},
        },
    )
    admitted = admit_display(
        [":3"],
        {},
        counts={":3": 24},
        max_clients=64,
        chrome_budget=8,
        auth_resolves=lambda _display: True,
    )
    assert admitted == ":3"
    with pytest.raises(XDisplayCapacityError):
        admit_display(
            [":3"],
            {},
            counts={":3": 25},
            max_clients=64,
            chrome_budget=8,
            auth_resolves=lambda _display: True,
        )
