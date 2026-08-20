"""X display capacity probe — mint refuse, log scrape, unix-table parse."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.x_display_capacity import (
    XDisplayCapacityError,
    count_x11_unix_clients,
    display_x11_socket_name,
    exhausted_message,
    listen_timeout_x_message,
    log_bytes_show_x_exhaustion,
    probe_x_display,
    require_chrome_headroom,
    x_display_wire_fields,
)

pytestmark = [pytest.mark.offline, pytest.mark.live_x_display]


def test_display_socket_name_strips_screen() -> None:
    assert display_x11_socket_name(":2") == "X2"
    assert display_x11_socket_name(":2.0") == "X2"
    assert display_x11_socket_name("2") == "X2"


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
