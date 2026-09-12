#!/usr/bin/env python3
"""Focus a COSMIC window by title / app_id over the raw Wayland wire (no launcher, no guessing).

Why raw wire: the GUI host (jupiter, cosmic-comp) has neither ``wayland-info`` nor
``pywayland``, yet it advertises ``ext_foreign_toplevel_list_v1`` (titles, app_ids)
and ``zcosmic_toplevel_manager_v1`` (``activate``) to ordinary clients — observed
2026-09-12 06:12Z with a registry dump. Every earlier raise attempt was blind:
``cursor --folder-uri`` cannot take focus on native Wayland (and once went to
Firefox), and driving the COSMIC launcher by keystrokes fuzzy-matched UMLet, then
launched a second Cursor IDE window. This helper *reads* the toplevel list and
activates exactly one match, or fails closed.

Invoke on the graphical host (SSH from io):
  WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000 \\
    python3 scripts/cosmic_focus_window.py list
    python3 scripts/cosmic_focus_window.py activate --app-id cursor --title-substr "[SSH: io]"
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time
from typing import Any

_REGISTRY, _CB_GLOBALS, _TL_LIST, _TL_INFO, _TL_MGR, _SEAT, _CB_LIST = (
    2,
    3,
    4,
    5,
    6,
    7,
    8,
)
_NEXT_CLIENT_ID = 9  # client ids must be allocated sequentially after the fixed binds
_STATE_ACTIVATED = (
    2  # zcosmic_toplevel_handle_v1.state enum: maximized 0, minimized 1, activated 2
)
_WANTED = {
    "ext_foreign_toplevel_list_v1": (_TL_LIST, 1),
    "zcosmic_toplevel_info_v1": (_TL_INFO, 2),
    "zcosmic_toplevel_manager_v1": (_TL_MGR, 1),
    "wl_seat": (_SEAT, 1),
}


def _string(text: str) -> bytes:
    raw = text.encode("utf-8") + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _read_string(body: bytes, off: int) -> tuple[str, int]:
    (ln,) = struct.unpack_from("<I", body, off)
    return body[off + 4 : off + 4 + ln - 1].decode("utf-8", "replace"), off + 4 + (
        (ln + 3) & ~3
    )


class Wire:
    """One Wayland connection; requests are packed by hand, events skipped by header size."""

    def __init__(self) -> None:
        run = os.environ.get("XDG_RUNTIME_DIR") or ""
        disp = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        if not run:
            raise SystemExit("XDG_RUNTIME_DIR unset — run on the graphical host")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(os.path.join(run, disp))
        self.sock.settimeout(4)
        self.buf = b""
        self.globals: dict[str, tuple[int, int]] = {}
        self.handles: dict[int, dict[str, Any]] = {}
        self.cosmic_of: dict[int, int] = {}  # cosmic handle id -> ext handle id
        self.errors: list[str] = []
        self.next_id = _NEXT_CLIENT_ID

    def alloc(self) -> int:
        """Next client object id — the wire rejects gaps (``Invalid new_id``)."""
        self.next_id += 1
        return self.next_id - 1

    def send(self, obj: int, op: int, payload: bytes = b"") -> None:
        self.sock.sendall(
            struct.pack("<II", obj, ((8 + len(payload)) << 16) | op) + payload
        )

    def roundtrip(self, callback_id: int) -> None:
        self.send(1, 0, struct.pack("<I", callback_id))  # wl_display.sync
        while True:
            for obj, op, body in self._events():
                self._handle(obj, op, body)
                if obj == callback_id and op == 0:
                    return
                if obj == 1 and op == 0:
                    raise SystemExit(
                        json.dumps({"ok": False, "wl_display_error": self.errors[-1]})
                    )

    def _events(self) -> list[tuple[int, int, bytes]]:
        try:
            chunk = self.sock.recv(65536)
        except TimeoutError as exc:
            raise SystemExit(
                json.dumps({"ok": False, "error": "compositor_timeout"})
            ) from exc
        if not chunk:
            raise SystemExit(json.dumps({"ok": False, "error": "compositor_closed"}))
        self.buf += chunk
        out: list[tuple[int, int, bytes]] = []
        while len(self.buf) >= 8:
            obj, so = struct.unpack_from("<II", self.buf, 0)
            size = so >> 16
            if len(self.buf) < size:
                break
            out.append((obj, so & 0xFFFF, self.buf[8:size]))
            self.buf = self.buf[size:]
        return out

    def _handle(self, obj: int, op: int, body: bytes) -> None:
        if os.environ.get("COSMIC_FOCUS_DEBUG"):
            print(
                f"event obj={obj} op={op} len={len(body)} {body[:24].hex()}",
                file=sys.stderr,
            )
        if obj == 1 and op == 0:  # wl_display.error(object_id, code, message)
            oid, code = struct.unpack_from("<II", body, 0)
            msg, _ = _read_string(body, 8)
            self.errors.append(f"object {oid} code {code}: {msg}")
        elif (
            obj == _REGISTRY and op == 0
        ):  # wl_registry.global(name, interface, version)
            (name,) = struct.unpack_from("<I", body, 0)
            iface, off = _read_string(body, 4)
            (ver,) = struct.unpack_from("<I", body, off)
            self.globals[iface] = (name, ver)
        elif (
            obj == _TL_LIST and op == 0
        ):  # ext_foreign_toplevel_list_v1.toplevel(new_id)
            (hid,) = struct.unpack_from("<I", body, 0)
            self.handles[hid] = {}
        elif obj in self.handles and op in (
            2,
            3,
            4,
        ):  # handle.title / app_id / identifier
            value, _ = _read_string(body, 0)
            self.handles[obj][("title", "app_id", "identifier")[op - 2]] = value
        elif (
            obj in self.cosmic_of and op == 8
        ):  # zcosmic_toplevel_handle_v1.state(array<u32>)
            (ln,) = struct.unpack_from("<I", body, 0)
            states = struct.unpack_from(f"<{ln // 4}I", body, 4)
            self.handles[self.cosmic_of[obj]]["activated"] = _STATE_ACTIVATED in states

    def bind(self, iface: str, obj_id: int, want_version: int) -> int:
        name, ver = self.globals[iface]
        version = min(ver, want_version)
        self.send(
            _REGISTRY,
            0,
            struct.pack("<I", name)
            + _string(iface)
            + struct.pack("<II", version, obj_id),
        )
        return version


def toplevels(wire: Wire) -> list[dict[str, Any]]:
    wire.send(1, 1, struct.pack("<I", _REGISTRY))  # wl_display.get_registry
    wire.roundtrip(_CB_GLOBALS)
    missing = [i for i in _WANTED if i not in wire.globals]
    if missing:
        raise SystemExit(
            json.dumps({"ok": False, "error": "globals_missing", "missing": missing})
        )
    for iface, (obj_id, ver) in _WANTED.items():
        wire.bind(iface, obj_id, ver)
    wire.roundtrip(_CB_LIST)
    return [
        {"handle": hid, **props}
        for hid, props in wire.handles.items()
        if props.get("title")
    ]


def read_states(wire: Wire) -> None:
    """Attach ``activated`` to every handle by opening its COSMIC counterpart (state events)."""
    for hid in list(wire.handles):
        cosmic_id = wire.alloc()
        wire.cosmic_of[cosmic_id] = hid
        wire.send(_TL_INFO, 1, struct.pack("<II", cosmic_id, hid))
    # cosmic-comp fills a new handle on its next refresh, not inside the request; the
    # first sync returns before any state event, so poll a few short roundtrips.
    for _ in range(8):
        wire.roundtrip(wire.alloc())
        if any("activated" in props for props in wire.handles.values()):
            return
        time.sleep(0.1)


def select(
    rows: list[dict[str, Any]], *, app_id: str | None, title_substr: list[str]
) -> list[dict[str, Any]]:
    """Case-insensitive app_id substring AND every title substring — all must hold."""
    out = []
    for row in rows:
        if app_id and app_id.lower() not in (row.get("app_id") or "").lower():
            continue
        title = (row.get("title") or "").lower()
        if all(s.lower() in title for s in title_substr):
            out.append(row)
    return out


def activate(wire: Wire, handle: int) -> None:
    # zcosmic_toplevel_info_v1.get_cosmic_toplevel(cosmic_toplevel new_id, foreign_toplevel object)
    cosmic_id = wire.alloc()
    wire.send(_TL_INFO, 1, struct.pack("<II", cosmic_id, handle))
    # zcosmic_toplevel_manager_v1.activate(toplevel, seat)
    wire.send(_TL_MGR, 2, struct.pack("<II", cosmic_id, _SEAT))
    wire.roundtrip(wire.alloc())


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "list", help="print every toplevel as JSON (title, app_id, identifier)"
    )
    a = sub.add_parser(
        "activate",
        help="focus the single toplevel matching --app-id and --title-substr",
    )
    a.add_argument("--app-id", default=None, help="app_id substring, e.g. cursor")
    a.add_argument(
        "--title-substr",
        action="append",
        default=[],
        help="title substring (repeatable, all must match)",
    )
    a.add_argument(
        "--pick",
        choices=("only", "first"),
        default="only",
        help="only: refuse ambiguity (default)",
    )
    args = p.parse_args()
    wire = Wire()
    rows = toplevels(wire)
    if args.cmd == "list":
        read_states(wire)
        rows = [{"handle": h, **p} for h, p in wire.handles.items() if p.get("title")]
        print(json.dumps({"ok": True, "count": len(rows), "toplevels": rows}, indent=1))
        return 0
    matches = select(rows, app_id=args.app_id, title_substr=args.title_substr)
    if not matches or (len(matches) > 1 and args.pick == "only"):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "no_unique_match",
                    "matches": matches,
                    "candidates": rows,
                }
            )
        )
        return 2
    activate(wire, matches[0]["handle"])
    read_states(wire)
    focused = bool(wire.handles[matches[0]["handle"]].get("activated"))
    ok = focused and not wire.errors
    print(
        json.dumps(
            {
                "ok": ok,
                "activated": matches[0],
                "focused": focused,
                "errors": wire.errors,
            }
        )
    )
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
