"""X display capacity probe — mint refuse, log scrape, unix-table parse."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from admission_common.qualified_scalar import SurfaceDecl

from claude_bundles.x_display_capacity import (
    _X_MAX_SCOPE,
    XDisplayCapacityError,
    attach_x_display_capacity,
    count_x11_unix_clients,
    display_x11_socket_name,
    listen_timeout_display_dead_message,
    listen_timeout_x_message,
    log_bytes_show_display_dead,
    log_bytes_show_x_exhaustion,
    probe_x_display,
    require_cdp_display_reachable,
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
        lambda proc_root=None: [
            ["Xvfb", ":2", "-auth", "/tmp/x", "-maxclients", "256"]
        ],
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


def test_launch_chrome_keeps_log_timeout_without_prelaunch_headroom(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_lane
    from claude_bundles.cdp_lane import LaneError

    log = tmp_path / "chrome.log"
    log.write_bytes(b"")
    monkeypatch.setattr(cdp_lane, "_LAUNCH_WAIT_S", 0.05)
    monkeypatch.setattr(
        cdp_lane, "_seed_profile", lambda _profile, account_id=None: None
    )
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


_ACCOUNTS_YAML_BASE = """
never:
  - ":1"
displays:
  ":2":
    owner: default
    kind: cdp_default
  ":3":
    owner: messages
    kind: messages
  ":4":
    owner: account-2
    kind: cdp_account
  ":8":
    owner: io
    kind: cursor
cdp_account_displays: {}
"""


def _write_accounts_config(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "accounts.yaml"
    path.write_text(_ACCOUNTS_YAML_BASE + extra, encoding="utf-8")
    return path


def test_cdp_display_candidates_default_single_display(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles.cdp_lane import cdp_display, cdp_display_candidates

    monkeypatch.setenv(
        "CDP_ACCOUNTS_CONFIG_PATH", str(_write_accounts_config(tmp_path))
    )
    monkeypatch.delenv("CDP_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    assert cdp_display() == ":2"
    assert cdp_display_candidates() == [":2"]
    assert ":3" not in cdp_display_candidates()


def test_cdp_display_candidates_account_two_never_default_displays(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles.cdp_lane import cdp_display_candidates

    extra = '\ncdp_account_displays:\n  account-2: ":4"\n'
    monkeypatch.setenv(
        "CDP_ACCOUNTS_CONFIG_PATH",
        str(_write_accounts_config(tmp_path, extra=extra)),
    )
    got = cdp_display_candidates("account-2")
    assert got == [":4"]
    for forbidden in (":2", ":1", ":3"):
        assert forbidden not in got


def test_cdp_display_remaps_cdp_display_colon_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles.cdp_lane import cdp_display, cdp_display_for_account

    monkeypatch.setenv(
        "CDP_ACCOUNTS_CONFIG_PATH", str(_write_accounts_config(tmp_path))
    )
    monkeypatch.setenv("CDP_DISPLAY", ":1")
    assert cdp_display() == ":2"
    assert cdp_display_for_account("default") == ":2"


def test_default_display_rejects_non_cdp_default_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles.cdp_lane import cdp_display_for_account
    from claude_bundles.cdp_registry.models import RegistryError

    monkeypatch.setenv(
        "CDP_ACCOUNTS_CONFIG_PATH", str(_write_accounts_config(tmp_path))
    )
    for bad in (":3", ":8"):
        monkeypatch.setenv("CDP_DISPLAY", bad)
        with pytest.raises(RegistryError, match="cdp_default"):
            cdp_display_for_account("default")


@pytest.mark.parametrize(
    "branch",
    ["register_lane_mint", "open_seat_relaunch", "dormant_unbound_relaunch"],
)
def test_tab_ask_when_x_headroom_below_one_chrome(
    branch: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.driving_seat import launch_or_defer
    from claude_bundles.cdp_registry.models import OperatorSeatDeferred
    from claude_bundles.x_display_capacity import XDisplayCapacityError

    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    host = {
        "registration_id": "host-eligible",
        "status": "active",
        "port": 9225,
        "display": ":2",
        "profile_suffix": "reg-h",
        "holder": "pool",
        "purpose": "tab_host",
        "started_at": 1.0,
    }
    seat = {
        "registration_id": "seat-dormant",
        "status": "dormant",
        "port": 9224,
        "display": ":2",
        "profile_suffix": "reg-seat",
        "chat_url": "https://claude.ai/cowork/cse_seat1",
        "parent_thread": "lane-1",
        "purpose": "operator-proxy",
        "seat_lane": "lane-1",
        "seat_bound_at": 1.0,
    }
    reg._store.write_active({"host-eligible": host, "seat-dormant": dict(seat)})

    def _raise_x() -> None:
        raise XDisplayCapacityError("X display :2 exhausted")

    monkeypatch.setattr(
        "claude_bundles.cdp_lane.is_listening",
        lambda port: port in (9224, 9225),
    )
    with pytest.raises(OperatorSeatDeferred) as caught:
        launch_or_defer(branch, _raise_x)
    assert caught.value.data["branch"] == branch
    after = reg._store.load_active()["seat-dormant"]
    assert after["chat_url"] == seat["chat_url"]
    assert after["profile_suffix"] == seat["profile_suffix"]

    from claude_bundles.cdp_registry.models import STATUS_TAB
    from claude_bundles.cdp_registry.tab_rows import open_tab_row_for_ask

    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.probe_x_display",
        lambda **kw: {
            "x_display": ":2",
            "x_clients": 62,
            "x_max_clients": 64,
            "x_headroom": 2,
            "x_exhausted": True,
            "x_chrome_client_budget": 8,
            "x_probe": "injected",
        },
    )
    mint_lock = tmp_path / "mint.lock"
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.tab_rows._TAB_MINT_LOCK",
        mint_lock,
    )
    with patch(
        "claude_bundles.cdp_lane.open_cdp_tab_target",
        return_value="tgt-low-x",
    ):
        tab = open_tab_row_for_ask(
            holder="ask-1",
            purpose="ask",
            mission_kind="root",
            parent_thread="lane-1",
        )
    assert tab.target_id == "tgt-low-x"
    assert tab.host_registration_id == "host-eligible"
    tab_row = reg._store.load_active()[tab.registration_id]
    assert tab_row["status"] == STATUS_TAB
