#!/usr/bin/env python3
"""Enumerate Wayland toplevel windows via ext_foreign_toplevel_list_v1.

Stdlib-only probe for COSMIC (and other compositors exposing the extension).
Written during agent-bus:10462 turn 35 to diagnose focus misdelivery; preserved
here as the substrate for a future focus gate (CURSOR_BRIDGE_FOCUS_VERIFY_CMD).

Usage on the graphical host:
  WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000 \\
    python3 scripts/wl_toplevels.py
"""

from __future__ import annotations

import os
import socket
import struct
import sys


def _pad(b: bytes) -> bytes:
    return b + b"\0" * ((4 - len(b) % 4) % 4)


def _wstr(s: str) -> bytes:
    b = s.encode() + b"\0"
    return struct.pack("<I", len(b)) + _pad(b)


def _msg(obj: int, op: int, payload: bytes) -> bytes:
    return struct.pack("<II", obj, ((8 + len(payload)) << 16) | op) + payload


def _connect() -> socket.socket:
    display = os.environ.get("WAYLAND_DISPLAY")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not display or not runtime:
        raise SystemExit("WAYLAND_DISPLAY and XDG_RUNTIME_DIR required")
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(os.path.join(runtime, display))
    return sock


def _drain(sock: socket.socket, buf: bytes) -> bytes:
    try:
        while True:
            data = sock.recv(65536)
            if not data:
                break
            buf += data
    except OSError:
        pass
    return buf


def _parse_globals(buf: bytes) -> dict[str, tuple[int, int]]:
    globals_: dict[str, tuple[int, int]] = {}
    i = 0
    while i + 8 <= len(buf):
        obj, so = struct.unpack_from("<II", buf, i)
        size = so >> 16
        op = so & 0xffff
        if obj == 2 and op == 0:
            name, slen = struct.unpack_from("<II", buf, i + 8)
            iface = buf[i + 16 : i + 16 + slen - 1].decode()
            ver = struct.unpack_from(
                "<I", buf, i + 16 + len(_pad(buf[i + 16 : i + 16 + slen]))
            )[0]
            globals_[iface] = (name, ver)
        i += size
    return globals_


def _parse_toplevels(buf: bytes) -> list[dict[str, str]]:
    handles: dict[int, dict[str, str]] = {}
    i = 0
    while i + 8 <= len(buf):
        obj, so = struct.unpack_from("<II", buf, i)
        size = so >> 16
        op = so & 0xffff
        body = buf[i + 8 : i + size]
        if obj == 4 and op == 0:
            hid = struct.unpack_from("<I", body)[0]
            handles[hid] = {}
        elif obj in handles:
            if op in (2, 3, 4):
                slen = struct.unpack_from("<I", body)[0]
                val = body[4 : 4 + slen - 1].decode(errors="replace")
                handles[obj][{2: "title", 3: "app_id", 4: "identifier"}[op]] = val
        i += size
    return list(handles.values())


def list_toplevels() -> tuple[list[dict[str, str]], dict[str, tuple[int, int]]]:
    sock = _connect()
    sock.send(_msg(1, 1, struct.pack("<I", 2)))  # get_registry -> 2
    sock.send(_msg(1, 0, struct.pack("<I", 3)))  # sync -> 3
    sock.settimeout(1.5)
    buf = _drain(sock, b"")
    globals_ = _parse_globals(buf)

    want = "ext_foreign_toplevel_list_v1"
    if want not in globals_:
        raise SystemExit(f"{want} not advertised by compositor")
    name, ver = globals_[want]
    sock.send(
        _msg(2, 0, struct.pack("<I", name) + _wstr(want) + struct.pack("<II", 1, 4))
    )  # bind -> 4
    sock.send(_msg(1, 0, struct.pack("<I", 5)))  # sync -> 5
    buf = _drain(sock, b"")
    return _parse_toplevels(buf), globals_


def main() -> int:
    toplevels, globals_ = list_toplevels()
    for entry in toplevels:
        print(entry)
    print("zcosmic_toplevel_info_v1:", globals_.get("zcosmic_toplevel_info_v1"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
