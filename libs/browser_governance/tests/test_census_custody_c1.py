"""Offline acceptance tests for C1 census custody: labels, duplicate owners, emit path."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
from browser_governance.census import collect_local_browser_census, run_census_and_emit


def _meminfo(proc_root: Path) -> None:
    proc_root.mkdir(parents=True, exist_ok=True)
    (proc_root / "meminfo").write_text(
        "MemAvailable: 8192000 kB\nSwapTotal: 4194304 kB\nSwapFree: 2097152 kB\n",
        encoding="utf-8",
    )


def _pid(proc_root: Path, pid: str, cmdline: bytes, *, rss: int = 1000, pss: int = 800) -> None:
    d = proc_root / pid
    d.mkdir()
    (d / "cmdline").write_bytes(cmdline)
    (d / "status").write_text(f"Name:\tchrome\nVmRSS:\t{rss} kB\nThreads:\t1\n", encoding="utf-8")
    (d / "smaps_rollup").write_text(f"Pss:\t{pss} kB\n", encoding="utf-8")


def test_census_labels_custody_and_counts_unowned(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(parents=True, exist_ok=True)
    _meminfo(proc_root)
    _pid(proc_root, "1", b"/opt/chrome\0--user-data-dir=/home/u/.config/chatgpt-io\0")
    _pid(proc_root, "2", b"/opt/chrome\0--user-data-dir=/tmp/claude-ai-chrome-profile-reg-abcd\0")
    _pid(proc_root, "3", b"/opt/chrome\0--user-data-dir=/tmp/stray\0")
    census = collect_local_browser_census(proc_root=proc_root, disk_path=str(tmp_path), cdp_ports=())
    profiles = census["details"]["profiles"]
    labels = {path: row.get("custody_class") for path, row in profiles.items()}
    assert labels == {
        "/home/u/.config/chatgpt-io": "standing_operator_house",
        "/tmp/claude-ai-chrome-profile-reg-abcd": "pooled_cowork_clone",
        "/tmp/stray": "unowned",
    }
    assert census.get("unowned_profile_count") == 1


def test_census_flags_duplicate_root_for_one_profile(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _meminfo(proc_root)
    prof = b"/opt/chrome\0--user-data-dir=/tmp/dup-profile\0"
    _pid(proc_root, "10", prof)
    _pid(proc_root, "11", prof)
    census = collect_local_browser_census(proc_root=proc_root, disk_path=str(tmp_path), cdp_ports=())
    assert census["duplicate_owner_count"] == 1
    assert "/tmp/dup-profile" in census["duplicate_owner_profiles"]


def test_census_children_not_owners_and_xvfb_once(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _meminfo(proc_root)
    _pid(proc_root, "10", b"/opt/google-chrome\0--user-data-dir=/tmp/family\0")
    _pid(proc_root, "11", b"/opt/google-chrome\0--type=renderer\0--user-data-dir=/tmp/family\0")
    _pid(proc_root, "12", b"/opt/google-chrome\0--type=renderer\0--user-data-dir=/tmp/family\0")
    xv = proc_root / "20"
    xv.mkdir()
    (xv / "cmdline").write_bytes(b"/usr/bin/Xvfb\0:9\0")
    (xv / "status").write_text("Name:\tXvfb\nVmRSS:\t64000 kB\nThreads:\t1\n", encoding="utf-8")
    census = collect_local_browser_census(proc_root=proc_root, disk_path=str(tmp_path), cdp_ports=())
    assert census["duplicate_owner_count"] == 0
    assert census["details"]["xvfb_rss_mib"]["9"] == pytest.approx(64000 / 1024.0)


def test_census_emit_forwards_custody_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc_root = tmp_path / "proc"
    _meminfo(proc_root)
    _pid(proc_root, "1", b"/opt/chrome\0--user-data-dir=/home/u/.config/chatgpt-io\0")
    _pid(proc_root, "2", b"/opt/chrome\0--user-data-dir=/tmp/stray\0")
    sock_path = tmp_path / "ev.sock"
    assert len(os.fsencode(sock_path)) < 100, "AF_UNIX path too long: use a shorter --basetemp"
    monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "1")
    monkeypatch.setenv("ULG_EVENTS_ENABLE", "1")
    monkeypatch.setenv("EVENTS_INGEST_SOCK", str(sock_path))
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    chunks: list[bytes] = []
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(sock_path))
        srv.listen(1)
        srv.settimeout(3.0)
        census = run_census_and_emit(
            host="test",
            proc_root=proc_root,
            disk_path=str(tmp_path),
            cdp_ports=(),
            source="c1-harness",
        )
        try:
            conn, _ = srv.accept()
        except TimeoutError:
            pytest.fail(
                "no connection on the ingest socket: "
                "run_census_and_emit did not reach emit_browser_event"
            )
        with conn:
            conn.settimeout(3.0)
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                chunks.append(data)
    finally:
        srv.close()
    assert census is not None
    assert chunks, "the ingest socket accepted a connection but received no bytes"
    line = json.loads(b"".join(chunks).decode("utf-8").splitlines()[0])
    assert line["signal"] == "browser.resource.census"
    assert line["source"] == "c1-harness"
    payload = line["payload"]
    assert payload.get("unowned_profile_count") == census["unowned_profile_count"] == 1
    assert payload.get("duplicate_owner_count") == census["duplicate_owner_count"] == 0
    profiles = payload["details"]["profiles"]
    labels = {path: row.get("custody_class") for path, row in profiles.items()}
    assert labels == {
        "/home/u/.config/chatgpt-io": "standing_operator_house",
        "/tmp/stray": "unowned",
    }


def test_census_unprofiled_bucket_is_not_a_profile(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _meminfo(proc_root)
    _pid(proc_root, "30", b"/opt/chrome\0--no-first-run\0")
    _pid(
        proc_root,
        "31",
        b"/opt/google/chrome/chrome_crashpad_handler\0"
        b"--monitor-self-annotation=ptype=crashpad-handler\0--database=/tmp/crash\0",
    )
    _pid(proc_root, "32", b"/opt/chrome\0--user-data-dir=/tmp/stray\0")
    census = collect_local_browser_census(proc_root=proc_root, disk_path=str(tmp_path), cdp_ports=())
    assert census.get("duplicate_owner_count") == 0
    assert census.get("duplicate_owner_profiles") == []
    profiles = census["details"]["profiles"]
    labels = {path: row.get("custody_class") for path, row in profiles.items()}
    assert labels == {"/tmp/stray": "unowned", "__unprofiled__": None}
    assert census.get("unowned_profile_count") == 1


def test_census_classifies_boundary_paths(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _meminfo(proc_root)
    session = "/home/box/chrome-sessions/sess-0001"
    _pid(proc_root, "40", b"/opt/chrome\0--user-data-dir=/home/u/.config/chatgpt-io\0")
    _pid(proc_root, "41", b"/opt/chrome\0--user-data-dir=/tmp/notchatgpt-io\0")
    _pid(proc_root, "42", b"/opt/chrome\0--user-data-dir=" + session.encode() + b"\0")
    census = collect_local_browser_census(proc_root=proc_root, disk_path=str(tmp_path), cdp_ports=())
    profiles = census["details"]["profiles"]
    labels = {path: row.get("custody_class") for path, row in profiles.items()}
    assert labels == {
        "/home/u/.config/chatgpt-io": "standing_operator_house",
        "/tmp/notchatgpt-io": "unowned",
        session: "ephemeral_isolated_session",
    }
    assert census.get("unowned_profile_count") == 1


def test_census_rows_carry_standing_owner_id(tmp_path: Path) -> None:
    from browser_governance.contract import (
        STANDING_HOUSES,
        custody_class_for_profile,
        standing_owner_id_for_profile,
    )

    house = "/home/u/.config/chatgpt-io"
    clone = "/tmp/claude-ai-chrome-profile-reg-abcd"
    lookalike = "/tmp/notchatgpt-io"
    session = "/home/box/chrome-sessions/sess-0001"
    expected = STANDING_HOUSES["chatgpt-io"]
    assert isinstance(expected, str)
    assert expected
    assert custody_class_for_profile(house) == "standing_operator_house"
    assert standing_owner_id_for_profile(house) == expected
    assert standing_owner_id_for_profile("/tmp/stray") is None
    for other in (clone, lookalike, session):
        assert standing_owner_id_for_profile(other) is None, other
    proc_root = tmp_path / "proc"
    _meminfo(proc_root)
    _pid(proc_root, "50", b"/opt/chrome\0--user-data-dir=/home/u/.config/chatgpt-io\0")
    _pid(proc_root, "51", b"/opt/chrome\0--user-data-dir=/tmp/stray\0")
    _pid(proc_root, "52", b"/opt/chrome\0--user-data-dir=" + clone.encode() + b"\0")
    _pid(proc_root, "53", b"/opt/chrome\0--user-data-dir=" + lookalike.encode() + b"\0")
    _pid(proc_root, "54", b"/opt/chrome\0--user-data-dir=" + session.encode() + b"\0")
    _pid(proc_root, "55", b"/opt/chrome\0--no-first-run\0")
    census = collect_local_browser_census(proc_root=proc_root, disk_path=str(tmp_path), cdp_ports=())
    profiles = census["details"]["profiles"]
    owners = {path: row.get("owner_id") for path, row in profiles.items()}
    assert owners == {
        house: expected,
        "/tmp/stray": None,
        clone: None,
        lookalike: None,
        session: None,
        "__unprofiled__": None,
    }
