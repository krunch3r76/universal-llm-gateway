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
from collections.abc import Iterator
from contextlib import contextmanager
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


def read_states(
    wire: Wire, *, target: int | None = None, timeout_s: float = 3.0
) -> None:
    """Attach ``activated`` to every handle by opening its COSMIC counterpart (state events).

    cosmic-comp fills a new handle on its next refresh, not inside the request, and a
    focus change after ``activate`` arrives as a later ``state`` event — so poll short
    roundtrips until ``target`` reports activated (or any state arrives when no target).
    """
    # one COSMIC handle per toplevel: the compositor reports state on the first one only
    for hid in list(wire.handles):
        if hid in wire.cosmic_of.values():
            continue
        cosmic_id = wire.alloc()
        wire.cosmic_of[cosmic_id] = hid
        wire.send(_TL_INFO, 1, struct.pack("<II", cosmic_id, hid))
    deadline = time.monotonic() + timeout_s
    while True:
        wire.roundtrip(wire.alloc())
        if target is not None:
            if wire.handles.get(target, {}).get("activated"):
                return
        elif any("activated" in props for props in wire.handles.values()):
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.15)


BROWSER_APP_ID_NEEDLES = ("firefox", "chromium", "google-chrome", "chrome", "brave", "vivaldi")


def _is_browser_app(app_id: str) -> bool:
    needle = (app_id or "").lower()
    return any(part in needle for part in BROWSER_APP_ID_NEEDLES)


def exclusive_cursor_keyboard(
    rows: list[dict[str, Any]],
    *,
    identifier: str = "",
    title: str = "",
) -> dict[str, Any]:
    """Seat keyboard is on the chosen Cursor toplevel and not on a browser.

    cosmic-comp can mark the Cursor handle ``activated`` while Firefox (claude.ai
    Cowork) still holds the seat. uinput then types into the browser. Specimen:
    orion-node 2026-10-05, identifier 1GTsuA4NedTMQ1fchPkIG5KKoi8cQ82R, launch
    ``ok=true`` while maestro CSE received Ctrl+Enter.
    """
    activated = [row for row in rows if row.get("activated")]
    browsers = [row for row in activated if _is_browser_app(str(row.get("app_id") or ""))]
    want_id = (identifier or "").strip()
    want_title = (title or "").strip()

    def _is_chosen(row: dict[str, Any]) -> bool:
        if "cursor" not in str(row.get("app_id") or "").lower():
            return False
        if want_id and str(row.get("identifier") or "") == want_id:
            return True
        if want_title and str(row.get("title") or "") == want_title:
            return True
        return not want_id and not want_title

    cursor_hit = [row for row in activated if _is_chosen(row)]
    if browsers:
        return {
            "ok": False,
            "reason": "browser_activated",
            "activated": activated,
            "browsers": browsers,
        }
    if not cursor_hit:
        return {
            "ok": False,
            "reason": "cursor_not_activated",
            "activated": activated,
        }
    return {"ok": True, "reason": "exclusive_cursor", "activated": activated, "cursor": cursor_hit[0]}


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
    wire.cosmic_of[cosmic_id] = handle  # its state events are the activation proof
    wire.send(_TL_INFO, 1, struct.pack("<II", cosmic_id, handle))
    # zcosmic_toplevel_manager_v1.activate(toplevel, seat)
    wire.send(_TL_MGR, 2, struct.pack("<II", cosmic_id, _SEAT))
    wire.roundtrip(wire.alloc())


_INHIBIT_IFACE = "zwp_keyboard_shortcuts_inhibit_manager_v1"
_INHIBIT_MGR_VER = 1
_INHIBIT_EVENT_ACTIVE = 0
_INHIBIT_EVENT_INACTIVE = 1


def _bind_inhibit_globals(wire: Wire) -> tuple[int, int, int]:
    """Return (compositor_id, seat_id, inhibit_manager_id). Fail closed if manager missing.

    Inhibit does not bind the fixed toplevel ids 3–8 that ``list``/``activate`` use.
    After ``get_registry(2)`` the next client id must be 3 — jumping to
    ``_NEXT_CLIENT_ID`` (9) is a gap and Cosmic rejects it as ``Invalid new_id: 9``
    (a:38402).
    """
    wire.send(1, 1, struct.pack("<I", _REGISTRY))
    wire.next_id = _REGISTRY + 1
    cb = wire.alloc()
    wire.roundtrip(cb)
    missing = [i for i in ("wl_compositor", "wl_seat", _INHIBIT_IFACE) if i not in wire.globals]
    if missing:
        raise SystemExit(
            json.dumps({"ok": False, "error": "inhibit_globals_missing", "missing": missing})
        )
    compositor = wire.alloc()
    seat = wire.alloc()
    mgr = wire.alloc()
    wire.bind("wl_compositor", compositor, 4)
    wire.bind("wl_seat", seat, 7)
    wire.bind(_INHIBIT_IFACE, mgr, _INHIBIT_MGR_VER)
    wire.roundtrip(wire.alloc())
    return compositor, seat, mgr


def _wait_inhibit_active(wire: Wire, inhibit_id: int, *, timeout_s: float = 4.0) -> str | None:
    """Return None when active, else a failure reason string."""
    deadline = time.monotonic() + timeout_s
    active = False
    while time.monotonic() < deadline:
        cb = wire.alloc()
        wire.send(1, 0, struct.pack("<I", cb))
        done = False
        while not done:
            for obj, op, body in wire._events():
                wire._handle(obj, op, body)
                if obj == inhibit_id:
                    if op == _INHIBIT_EVENT_ACTIVE:
                        active = True
                    elif op == _INHIBIT_EVENT_INACTIVE:
                        return "inactive_before_active"
                if obj == cb and op == 0:
                    done = True
                    break
            if not done:
                try:
                    chunk = wire.sock.recv(65536)
                except TimeoutError:
                    break
                if not chunk:
                    return "compositor_closed"
                wire.buf += chunk
        if active:
            return None
        time.sleep(0.05)
    return "no_active_event"


def keyboard_shortcuts_inhibit_hold(*, hold_stdin: bool = True) -> dict[str, Any]:
    """Acquire zwp_keyboard_shortcuts_inhibit until stdin closes (Route 1 hop window).

    Creates a committed wl_surface, inhibits compositor shortcuts on the default seat,
    prints a one-line JSON verdict on stdout, then blocks until EOF on stdin (when
    ``hold_stdin``) or returns immediately after acquire (for unit tests).
    """
    wire = Wire()
    compositor, seat, mgr = _bind_inhibit_globals(wire)
    surface = wire.alloc()
    inhibit = wire.alloc()
    wire.send(compositor, 0, struct.pack("<I", surface))  # wl_compositor.create_surface
    wire.send(mgr, 1, struct.pack("<III", inhibit, seat, surface))  # inhibit(new_id, seat, surface)
    wire.send(surface, 6, b"")  # wl_surface.commit — surface must exist before keys route
    problem = _wait_inhibit_active(wire, inhibit)
    if problem:
        wire.send(inhibit, 0, b"")
        wire.send(surface, 0, b"")
        raise SystemExit(
            json.dumps({"ok": False, "error": "inhibit_not_active", "reason": problem})
        )
    print(json.dumps({"ok": True, "state": "active", "route": 1}), flush=True)
    outcome: dict[str, Any] = {"ok": True, "state": "released"}
    try:
        if hold_stdin and not sys.stdin.isatty():
            import select

            while True:
                r, _, _ = select.select([sys.stdin, wire.sock], [], [], 0.5)
                if wire.sock in r:
                    for obj, op, body in wire._events():
                        wire._handle(obj, op, body)
                        if obj == inhibit and op == _INHIBIT_EVENT_INACTIVE:
                            outcome = {
                                "ok": False,
                                "error": "inhibit_inactive",
                                "phase": "focus",
                            }
                            return outcome
                if sys.stdin in r and not sys.stdin.read(1):
                    break
    finally:
        wire.send(inhibit, 0, b"")  # zwp_keyboard_shortcuts_inhibit_v1.destroy
        wire.send(surface, 0, b"")  # wl_surface.destroy
        try:
            wire.roundtrip(wire.alloc())
        except SystemExit:
            pass
    return outcome


@contextmanager
def keyboard_shortcuts_inhibit_session() -> Iterator[dict[str, Any]]:
    """In-process inhibit for tests; on a real host use ``inhibit-hold`` subprocess."""
    wire = Wire()
    compositor, seat, mgr = _bind_inhibit_globals(wire)
    surface = wire.alloc()
    inhibit = wire.alloc()
    wire.send(compositor, 0, struct.pack("<I", surface))
    wire.send(mgr, 1, struct.pack("<III", inhibit, seat, surface))
    wire.send(surface, 6, b"")
    problem = _wait_inhibit_active(wire, inhibit)
    if problem:
        wire.send(inhibit, 0, b"")
        wire.send(surface, 0, b"")
        raise RuntimeError(problem)
    meta = {"ok": True, "state": "active", "route": 1, "wire": wire, "inhibit": inhibit, "surface": surface}
    try:
        yield meta
    finally:
        wire.send(inhibit, 0, b"")
        wire.send(surface, 0, b"")
        try:
            wire.roundtrip(wire.alloc())
        except SystemExit:
            pass


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
    ac = sub.add_parser(
        "assert-cursor",
        help="fail if a browser is activated or the named Cursor toplevel is not",
    )
    ac.add_argument("--identifier", default="")
    ac.add_argument("--title", default="")
    sub.add_parser(
        "inhibit-hold",
        help="hold zwp_keyboard_shortcuts_inhibit until stdin EOF (Route 1 glass hop)",
    )
    args = p.parse_args()
    if args.cmd == "inhibit-hold":
        result = keyboard_shortcuts_inhibit_hold(hold_stdin=True)
        print(json.dumps(result), flush=True)
        return 0 if result.get("ok") else 3
    wire = Wire()
    rows = toplevels(wire)
    if args.cmd == "list":
        read_states(wire)
        rows = [{"handle": h, **p} for h, p in wire.handles.items() if p.get("title")]
        print(json.dumps({"ok": True, "count": len(rows), "toplevels": rows}, indent=1))
        return 0
    if args.cmd == "assert-cursor":
        read_states(wire)
        rows = [{"handle": h, **p} for h, p in wire.handles.items() if p.get("title")]
        verdict = exclusive_cursor_keyboard(
            rows, identifier=args.identifier, title=args.title
        )
        print(json.dumps(verdict))
        return 0 if verdict.get("ok") else 3
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
    read_states(wire, target=matches[0]["handle"])
    rows = [{"handle": h, **p} for h, p in wire.handles.items() if p.get("title")]
    focused = bool(wire.handles[matches[0]["handle"]].get("activated"))
    exclusive = exclusive_cursor_keyboard(
        rows,
        identifier=str(matches[0].get("identifier") or ""),
        title=str(matches[0].get("title") or ""),
    )
    ok = focused and bool(exclusive.get("ok")) and not wire.errors
    print(
        json.dumps(
            {
                "ok": ok,
                "activated": matches[0],
                "focused": focused,
                "exclusive": exclusive,
                "errors": wire.errors,
            }
        )
    )
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
