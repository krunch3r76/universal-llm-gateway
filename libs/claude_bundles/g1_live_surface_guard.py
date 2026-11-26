"""G1 live-surface guard — audit-hook enforcement (c3 + c5/c6 I5/I9)."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GuardViolation = type("GuardViolation", (BaseException,), {})

G1_PERMITTED_PORT_RANGE = range(19100, 19110)

_DENY_EXCEPTIONS: dict[str, type[BaseException]] = {
    "open": PermissionError,
    "open_write": PermissionError,
    "mkdir": PermissionError,
    "rename": PermissionError,
    "remove": PermissionError,
    "rmdir": PermissionError,
    "scandir": PermissionError,
    "connect": ConnectionRefusedError,
    "bind": ConnectionRefusedError,
    "kill": ProcessLookupError,
    "killpg": ProcessLookupError,
    "pthread_kill": ProcessLookupError,
    "spawn": PermissionError,
}


@dataclass
class _GuardState:
    armed: bool = False
    install_token: str = ""
    ledger: list[dict[str, Any]] = field(default_factory=list)
    allowed_pids: set[int] = field(default_factory=set)
    leg_tmp: str = ""
    permitted_devices: list[str] = field(default_factory=list)
    hook_installed: bool = False
    session_receipt_emitted: bool = False

_STATE: _GuardState | None = None
_ORIG_KILL = None
_SESSION_EMITTER = None


class _SpawnPermit:
    def __init__(self) -> None:
        self._depth = 0

    def is_set(self) -> bool:
        return self._depth > 0

    def __enter__(self) -> _SpawnPermit:
        self._depth += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._depth -= 1


_spawn_permit = _SpawnPermit()


def _state() -> _GuardState:
    if _STATE is None or not _STATE.armed:
        raise RuntimeError("guard not installed")
    return _STATE


def _record(kind: str, detail: dict[str, Any], *, permitted: bool = False) -> None:
    row = {"kind": kind, "permitted": permitted, **detail}
    _state().ledger.append(row)


def _leg_tmp() -> str:
    return _state().leg_tmp


def allowed_write_prefixes() -> tuple[str, ...]:
    lt = _leg_tmp()
    return (lt,) if lt else ()


def permitted_devices() -> tuple[str, ...]:
    return tuple(_state().permitted_devices)


def _inside_leg(path: str | os.PathLike[str]) -> bool:
    raw = os.fspath(path)
    if not os.path.isabs(raw):
        raw = os.path.abspath(raw)
    resolved = os.path.realpath(raw)
    root = os.path.realpath(_leg_tmp())
    return resolved == root or resolved.startswith(root + os.sep)


def _port_allowed(port: int) -> bool:
    return port in G1_PERMITTED_PORT_RANGE


def _uds_allowed(path: str) -> bool:
    return _inside_leg(path)


def _is_permitted_device_path(path: str) -> bool:
    decoded = os.fsdecode(path)
    return decoded in _state().permitted_devices


def _deny(kind: str, detail: dict[str, Any]) -> None:
    _record(kind, detail, permitted=False)
    exc = _DENY_EXCEPTIONS.get(kind, PermissionError)
    raise exc(f"live primitive blocked: {kind}")


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    if _STATE is None or not _STATE.armed:
        return
    if event == "os.kill":
        pid = int(args[0])
        if pid in _STATE.allowed_pids:
            return
        _deny("kill", {"pid": pid, "sig": args[1] if len(args) > 1 else None})
    elif event == "os.killpg":
        _deny("killpg", {"pgid": args[0] if args else None})
    elif event == "signal.pthread_kill":
        _deny("pthread_kill", {"thread": args[0] if args else None})
    elif event == "socket.bind":
        if len(args) >= 2:
            address = args[1]
            if isinstance(address, tuple) and len(address) >= 2:
                port = address[1]
                if isinstance(port, int):
                    if _port_allowed(port):
                        _record("bind", {"address": address}, permitted=True)
                        return
                    _deny("bind", {"address": address})
    elif event in ("socket.connect", "socket.__new__"):
        if event == "socket.connect" and len(args) >= 2:
            sock, address = args[0], args[1]
            fam = getattr(sock, "family", socket.AF_INET)
            if fam == socket.AF_UNIX and isinstance(address, str):
                if _uds_allowed(address):
                    _record("connect", {"address": address, "family": "AF_UNIX"}, permitted=True)
                    return
                _deny("connect", {"address": address, "family": "AF_UNIX"})
                return
            if isinstance(address, tuple) and len(address) >= 2:
                host, port = address[0], address[1]
                if host in ("127.0.0.1", "::1", "localhost") and isinstance(port, int):
                    if _port_allowed(port):
                        _record("connect", {"address": address}, permitted=True)
                        return
                _deny("connect", {"address": address})
    elif event == "open":
        path = args[0] if args else ""
        if isinstance(path, int):
            return
        raw_flags = args[2] if len(args) > 2 else os.O_RDONLY
        mode = args[1] if len(args) > 1 else ""
        flags = int(raw_flags) if isinstance(raw_flags, int) else os.O_RDONLY
        writing = False
        if isinstance(mode, str):
            writing = any(ch in mode for ch in ("w", "a", "+", "x"))
        else:
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        pstr = os.fsdecode(path)
        if writing:
            if _is_permitted_device_path(pstr):
                _record("open_write", {"path": pstr, "flags": raw_flags, "device": True}, permitted=True)
                return
            if not _inside_leg(pstr):
                _deny("open_write", {"path": pstr, "flags": raw_flags})
            return
        if pstr.startswith("/proc"):
            _deny("open", {"path": pstr})
    elif event == "os.rename":
        src, dst = (args[0] if args else ""), (args[1] if len(args) > 1 else "")
        if (src and not _inside_leg(str(src))) or (dst and not _inside_leg(str(dst))):
            _deny("rename", {"src": str(src), "dst": str(dst)})
    elif event in ("os.mkdir", "os.rmdir", "os.remove"):
        path = args[0] if args else ""
        if path and not _inside_leg(str(path)):
            _deny(event.replace("os.", ""), {"path": str(path)})
    elif event == "os.listdir" and args and str(args[0]).startswith("/proc"):
        _deny("scandir", {"path": str(args[0])})
    elif event in ("subprocess.Popen", "subprocess.run", "subprocess.check_output"):
        if _spawn_permit.is_set():
            return
        argv = args[0] if args else ()
        _deny("spawn", {"argv": list(argv) if argv else []})


def _parse_basetemp(argv: Sequence[str]) -> str | None:
    for i, tok in enumerate(argv):
        if tok == "--basetemp" and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--basetemp="):
            return tok.split("=", 1)[1]
    return None


def _write_receipt_line(prefix: str, payload: dict[str, Any]) -> None:
    line = f"{prefix} {json.dumps(payload, separators=(',', ':'), sort_keys=True)}\n"
    buf = line.encode()
    off = 0
    while off < len(buf):
        off += os.write(2, buf[off:])


def install() -> None:
    global _STATE, _ORIG_KILL, _SESSION_EMITTER
    if _STATE is not None and _STATE.armed:
        return
    if os.environ.get("ULG_G1_GUARD") != "1":
        return

    cached_tempdir = tempfile.gettempdir()

    bt = _parse_basetemp(sys.argv)
    if bt is None:
        raise GuardViolation("guard refuses: no --basetemp in argv")
    basetemp = os.path.abspath(bt)
    leg_tmp = os.path.abspath(os.path.dirname(basetemp))
    gt = os.path.abspath(cached_tempdir)
    guard_dir = os.path.abspath(os.path.dirname(__file__))
    refused: list[str] = []
    if leg_tmp == "/":
        refused.append("root_is_slash")
    if gt == leg_tmp or gt.startswith(leg_tmp + os.sep):
        refused.append("leg_tmp_covers_system_temp")
    if leg_tmp == guard_dir or leg_tmp.startswith(guard_dir + os.sep):
        refused.append("leg_tmp_covers_guard_dir")
    devnull = os.devnull
    try:
        if os.path.isdir(devnull):
            refused.append("devnull_is_directory")
    except OSError:
        refused.append("devnull_unusable")
    if refused:
        raise GuardViolation(f"guard refuses derived leg_tmp {leg_tmp!r}: {refused}")

    _STATE = _GuardState(
        leg_tmp=leg_tmp,
        install_token=uuid.uuid4().hex,
        permitted_devices=[devnull],
    )
    _ORIG_KILL = os.kill
    sys.addaudithook(_audit_hook)
    _STATE.hook_installed = True
    _STATE.armed = True

    install_payload = {
        "permitted_prefixes": [leg_tmp],
        "permitted_devices": list(_STATE.permitted_devices),
        "argv_basetemp": bt,
        "install_token": _STATE.install_token,
        "guard_id": id(_STATE),
        "guard_file": __file__,
        "refusal_checks": refused or ["ok"],
        "sys_dont_write_bytecode": sys.dont_write_bytecode,
        "sys_pycache_prefix": sys.pycache_prefix,
        "sys_argv": list(sys.argv),
        "tempfile_gettempdir": cached_tempdir,
    }
    try:
        install_payload["guard_file_sha256"] = __import__("hashlib").sha256(
            Path(__file__).read_bytes()
        ).hexdigest()
    except OSError:
        install_payload["guard_file_sha256"] = "unreadable"
    _write_receipt_line("G1-RECEIPT-INSTALL", install_payload)

    def _emit_session() -> None:
        if _STATE is None or _STATE.session_receipt_emitted:
            return
        _STATE.session_receipt_emitted = True
        counts: dict[str, int] = {}
        for row in _STATE.ledger:
            k = str(row.get("kind", "?"))
            counts[k] = counts.get(k, 0) + 1
        _write_receipt_line(
            "G1-RECEIPT-SESSION",
            {
                "ledger_row_counts": counts,
                "ledger_rows": list(_STATE.ledger),
            },
        )

    _SESSION_EMITTER = _emit_session
    import atexit

    atexit.register(_emit_session)


def emit_session_receipt(**extra: Any) -> None:
    if _SESSION_EMITTER is not None:
        _SESSION_EMITTER()
    elif _STATE is not None and not _STATE.session_receipt_emitted:
        counts: dict[str, int] = {}
        for row in _STATE.ledger:
            k = str(row.get("kind", "?"))
            counts[k] = counts.get(k, 0) + 1
        payload = {"ledger_row_counts": counts, "ledger_rows": list(_STATE.ledger)}
        payload.update(extra)
        _write_receipt_line("G1-RECEIPT-SESSION", payload)
        _STATE.session_receipt_emitted = True


def armed() -> bool:
    return _STATE is not None and _STATE.armed


def ledger() -> list[dict[str, Any]]:
    return _state().ledger


def rows(kind: str | None = None) -> list[dict[str, Any]]:
    led = ledger()
    if kind is None:
        return led
    return [r for r in led if r.get("kind") == kind]


def note_test_pid(pid: int) -> None:
    _state().allowed_pids.add(int(pid))


def spawn(argv: Sequence[str]) -> int:
    import subprocess

    with _spawn_permit:
        proc = subprocess.Popen(list(argv))
    _state().allowed_pids.add(proc.pid)
    _record("spawn", {"argv": list(argv), "pid": proc.pid}, permitted=True)
    return proc.pid


def records_only() -> bool:
    return False


def install_token() -> str:
    return _state().install_token
