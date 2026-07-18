"""Profile-keyed CDP browser-lane allocator for Jupiter Chrome.

Runs ON Jupiter (where the locks, profiles and Chrome live). A *lane* is a
``(profile, port)`` pair; the scarce, identity-bearing resource is the
**profile** (Chrome's ProcessSingleton admits one process per
``--user-data-dir``, and the cookie lineage is the anti-abuse surface), so the
lease keys on the profile suffix and the port is fungible metadata.

Lane states (Fable adjudication, thread 4917):
  active   — the profile flock is held by a live process (never reuse/kill)
  dangling — Chrome listening but no flock held (reuse-warm)
  free     — nothing listening

Enforcement invariants (each proven by a kernel experiment in the consult):
  * fcntl.flock only (never lockf / POSIX record locks — they do not interlock)
  * lockfiles are PERMANENT — never unlinked (rm -> new inode -> double-hold)
  * O_CLOEXEC on the lock fd so a detached Chrome never inherits the lease
  * Chrome (the resource) is detached and survives as a warm dangling lane;
    nothing that speaks CDP is ever detached — drivers die with the holder
  * acquisition is one process: alloc.lock -> (fresh: NB-claim suffix+profile
    flock, retry next -N) OR (canonical: profile flock queue OUTSIDE alloc.lock)
    -> attest/launch under alloc.lock -> write metadata -> drop alloc.lock ->
    run -> exit (releases the profile flock)

Events are advisory over this flock SOT; a crashed holder emits no release, so
consumers treat a missing release as UNKNOWN and re-probe ground truth.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

LANE_DIR = Path.home() / ".gateway" / "cdp-lanes"
ALLOC_LOCK = LANE_DIR / "alloc.lock"
PRIMARY_PROFILE = Path.home() / ".gateway" / "claude-ai-chrome-profile"

# 9222 is pinned outside the pool: the attended operator seat + web-fetcher are
# standing multi-consumers there, deliberately out of contract.
PORT_RANGE = range(9223, 9240)

# intent -> canonical profile suffix. --fresh mints "<suffix>-2", "-3", ...
INTENT_SUFFIX = {
    "ask": "ask",
    "fable": "fable-consult",
    "opus": "opus-consult",
}

_CHROME_BIN = "google-chrome"
_LAUNCH_WAIT_S = 20
_POLL_MS = 250


class LaneError(RuntimeError):
    """Base for lane-allocation failures."""


class LaneBusyError(LaneError):
    """The requested profile is actively leased and the queue timed out."""


@dataclass(frozen=True)
class LaneInfo:
    intent: str
    suffix: str
    port: int
    profile: Path
    cdp_url: str
    reused: bool


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #
def resolve_suffix(intent: str, *, fresh: bool, taken: set[str]) -> str:
    """Map an intent to a profile suffix; --fresh mints the next free -N clone.

    ``taken`` is the set of suffixes whose profile lease is currently held, used
    only to pick the escape-hatch clone index deterministically.
    """
    base = INTENT_SUFFIX.get(intent, intent)
    if not base or "/" in base or base.startswith("."):
        raise LaneError(f"invalid intent/suffix: {intent!r}")
    if not fresh:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def profile_for(suffix: str) -> Path:
    return PRIMARY_PROFILE.parent / f"{PRIMARY_PROFILE.name}-{suffix}"


def lock_path_for(suffix: str) -> Path:
    return LANE_DIR / f"{suffix}.lock"


def select_free_port(is_listening, exclude: set[int]) -> int:
    """Lowest in-range port that is not listening and not excluded.

    ``is_listening(port) -> bool`` is injected so this stays pure/testable.
    """
    for port in PORT_RANGE:
        if port in exclude:
            continue
        if not is_listening(port):
            return port
    raise LaneError(f"no free CDP port in {PORT_RANGE.start}-{PORT_RANGE.stop - 1}")


def seed_profile_rsync_argv(source: Path, dest: Path) -> list[str]:
    """rsync argv for seeding a lane profile from PRIMARY (OptGuide excluded)."""
    return [
        "rsync",
        "-a",
        "--exclude=Singleton*",
        "--exclude=lockfile",
        "--exclude=.org.chromium.*",
        "--exclude=OptGuide*",
        "--exclude=optimization_guide_model_store",
        f"{source}/",
        f"{dest}/",
    ]


def chrome_launch_argv(port: int, profile: Path) -> list[str]:
    """Chrome argv for a CDP lane launch."""
    return [
        _CHROME_BIN,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=OptimizationGuideOnDeviceModel",
    ]


def parse_chrome_lane(cmdline: str) -> tuple[int | None, str | None]:
    """Extract (port, user_data_dir) from a Chrome cmdline blob.

    Chrome rewrites /proc/<pid>/cmdline into a single space-joined string, so we
    scan tokens rather than assuming NUL separation. Renderer/gpu children carry
    --type= and are rejected by the caller.
    """
    port: int | None = None
    udd: str | None = None
    for tok in cmdline.replace("\x00", " ").split():
        if tok.startswith("--remote-debugging-port="):
            with contextlib.suppress(ValueError):
                port = int(tok.split("=", 1)[1])
        elif tok.startswith("--user-data-dir="):
            udd = tok.split("=", 1)[1]
    return port, udd


# --------------------------------------------------------------------------- #
# Ground-truth probes (Jupiter-local)
# --------------------------------------------------------------------------- #
def is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def chrome_port_for_profile(profile: Path) -> int | None:
    """Return the debugging port of a live Chrome owning ``profile``, or None."""
    want = os.path.realpath(profile)
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            blob = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        if "--type=" in blob or "--remote-debugging-port=" not in blob:
            continue
        port, udd = parse_chrome_lane(blob)
        if port is None or udd is None:
            continue
        if os.path.realpath(udd) == want and is_listening(port):
            return port
    return None


# --------------------------------------------------------------------------- #
# Lock primitives
# --------------------------------------------------------------------------- #
def _open_lock(path: Path) -> int:
    LANE_DIR.mkdir(parents=True, exist_ok=True)
    # O_CLOEXEC: a detached Chrome must never inherit and thus extend the lease.
    return os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)


@contextlib.contextmanager
def _alloc_lock() -> Iterator[None]:
    fd = _open_lock(ALLOC_LOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # serialize the choose+launch window
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _try_profile_lock_nb(suffix: str) -> int | None:
    """Non-blocking profile flock; returns fd on success, None if actively held."""
    fd = _open_lock(lock_path_for(suffix))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def _try_profile_lock(suffix: str, *, queue_timeout_s: float) -> int:
    """Acquire the profile flock (LOCK_EX|LOCK_NB), queueing until timeout."""
    fd = _open_lock(lock_path_for(suffix))
    deadline = time.monotonic() + max(queue_timeout_s, 0.0)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise LaneBusyError(
                    f"profile '{suffix}' is actively leased "
                    f"(queue timeout {queue_timeout_s}s); "
                    f"for a parallel lane use --fresh-profile"
                ) from None
            time.sleep(0.5)


def _claim_fresh_profile_lock(intent: str) -> tuple[int, str]:
    """Claim a fresh clone suffix under ``alloc.lock`` (caller must hold it).

    NB-acquires the profile flock and advances to the next ``-N`` index when the
    chosen suffix is already leased — prevents concurrent fresh callers from
    colliding on the same lowest free index (a:24854 / web review A1).
    """
    base = INTENT_SUFFIX.get(intent, intent)
    if not base or "/" in base or base.startswith("."):
        raise LaneError(f"invalid intent/suffix: {intent!r}")
    taken = held_suffixes()
    n = 2
    for _ in range(512):
        while f"{base}-{n}" in taken:
            n += 1
        suffix = f"{base}-{n}"
        fd = _try_profile_lock_nb(suffix)
        if fd is not None:
            return fd, suffix
        taken.add(suffix)
        n += 1
    raise LaneError(f"no free fresh profile for intent {intent!r}")


def _allocate_port_for_profile(
    suffix: str, profile: Path, *, launch: bool
) -> tuple[int, bool]:
    existing = chrome_port_for_profile(profile)
    if existing is not None:
        return existing, True
    if not launch:
        raise LaneError(
            f"no live Chrome for profile '{suffix}' and launch=False"
        )
    # Cross-exclude registry-reserved ports (F3 / thread 5262) so the legacy
    # intent allocator and cdp_registry cannot TOCTOU-collide on the same port.
    exclude = set(held_ports())
    with contextlib.suppress(Exception):
        from claude_bundles.cdp_registry import used_ports_snapshot

        exclude |= used_ports_snapshot()
    port = select_free_port(is_listening, exclude=exclude)
    _launch_chrome(port, profile)
    return port, False


def held_suffixes() -> set[str]:
    """Suffixes whose profile lease is currently held (LOCK_NB probe)."""
    taken: set[str] = set()
    if not LANE_DIR.exists():
        return taken
    for lf in LANE_DIR.glob("*.lock"):
        if lf.name == ALLOC_LOCK.name:
            continue
        fd = _open_lock(lf)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)  # free -> not held
        except OSError:
            taken.add(lf.stem)  # held by someone else
        finally:
            os.close(fd)
    return taken


# --------------------------------------------------------------------------- #
# Chrome lifecycle
# --------------------------------------------------------------------------- #
def _seed_profile(profile: Path) -> None:
    if (profile / "Default").exists():
        return
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        seed_profile_rsync_argv(PRIMARY_PROFILE, profile),
        check=False,
    )


def _launch_chrome(port: int, profile: Path) -> int:
    """Launch a detached Chrome (the warm resource) and wait until it listens."""
    _seed_profile(profile)
    log = f"/tmp/chrome-cdp-claude-ai-{port}.log"
    env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":1"))
    with open(log, "ab") as logf:
        proc = subprocess.Popen(
            chrome_launch_argv(port, profile),
            stdout=logf,
            stderr=logf,
            stdin=subprocess.DEVNULL,
            # Detach the resource; close_fds so it never inherits the lock fd.
            start_new_session=True,
            close_fds=True,
            env=env,
        )
    deadline = time.monotonic() + _LAUNCH_WAIT_S
    while time.monotonic() < deadline:
        if is_listening(port):
            return proc.pid
        time.sleep(_POLL_MS / 1000)
    raise LaneError(f"Chrome on :{port} did not reach CDP in {_LAUNCH_WAIT_S}s")


def _write_metadata(fd: int, info: dict) -> None:
    with contextlib.suppress(OSError):
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, (json.dumps(info) + "\n").encode())
        os.fsync(fd)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def acquire_lane(
    intent: str,
    *,
    fresh: bool = False,
    queue_timeout_s: float = 120.0,
    launch: bool = True,
) -> Iterator[LaneInfo]:
    """Acquire a profile-keyed lane for the whole ``with`` block.

    The profile flock is held until the block exits (or the process dies), which
    is precisely the "active" lifetime. Chrome is left running on exit so the
    lane degrades to a reusable *dangling* state.
    """
    lock_fd = -1
    try:
        if fresh:
            with _alloc_lock():
                lock_fd, suffix = _claim_fresh_profile_lock(intent)
                profile = profile_for(suffix)
                port, reused = _allocate_port_for_profile(
                    suffix, profile, launch=launch
                )
                _write_metadata(
                    lock_fd,
                    {
                        "suffix": suffix,
                        "intent": intent,
                        "port": port,
                        "pid": os.getpid(),
                        "started_at": time.time(),
                    },
                )
        else:
            suffix = resolve_suffix(intent, fresh=False, taken=held_suffixes())
            profile = profile_for(suffix)
            lock_fd = _try_profile_lock(suffix, queue_timeout_s=queue_timeout_s)
            with _alloc_lock():
                port, reused = _allocate_port_for_profile(
                    suffix, profile, launch=launch
                )
                _write_metadata(
                    lock_fd,
                    {
                        "suffix": suffix,
                        "intent": intent,
                        "port": port,
                        "pid": os.getpid(),
                        "started_at": time.time(),
                    },
                )
        info = LaneInfo(
            intent=intent,
            suffix=suffix,
            port=port,
            profile=profile,
            cdp_url=f"http://127.0.0.1:{port}",
            reused=reused,
        )
        _emit("cdp.lane.acquired", info)
        try:
            yield info
        finally:
            _emit("cdp.lane.released", info)
    finally:
        if lock_fd >= 0:
            # LOCK_UN + close: the profile returns to dangling; Chrome stays warm.
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def held_ports() -> list[int]:
    """Ports recorded by currently-held profile leases (metadata read)."""
    ports: list[int] = []
    for suffix in held_suffixes():
        with contextlib.suppress(Exception):
            meta = json.loads(lock_path_for(suffix).read_text() or "{}")
            if isinstance(meta.get("port"), int):
                ports.append(meta["port"])
    return ports


def _emit(signal: str, info: LaneInfo) -> None:
    """Advisory event emit — best-effort, silent on failure (no events sock)."""
    sock_path = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )
    payload = {
        "signal": signal,
        "source": "cdp-lane",
        "role": "observation",
        "scope": "node",
        "ts_unix_ms": int(time.time() * 1000),
        "payload": {
            "intent": info.intent,
            "suffix": info.suffix,
            "port": info.port,
            "reused": info.reused,
        },
    }
    with contextlib.suppress(Exception):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(sock_path)
            s.sendall((json.dumps(payload) + "\n").encode())
