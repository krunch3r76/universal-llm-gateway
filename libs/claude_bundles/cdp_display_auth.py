"""Resolve CDP Chrome XAUTHORITY from live truth, not a single path convention.

a:32225: per-display path convention diverged from the running Xvfb ``-auth``
(flat ``~/.gateway/cdp-xvfb/Xauthority``). Mint must discover what the server
accepts, then bind Chrome to that cookie — never inherit a parent shell's
XAUTHORITY by accident.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_XDPYINFO_TIMEOUT_S = 5.0
_XVFB_AUTH_RE = re.compile(r"(?:^|\s)-auth\s+(\S+)")


@dataclass(frozen=True)
class DisplayAuth:
    """Resolved cookie for one X display.

    ``path`` is None only when the display genuinely needs no client auth
    (``source=\"none\"``). Unresolvable required auth raises before mint.
    """

    path: Path | None
    source: str  # per_display | flat | live_xvfb | none
    required: bool = True


class DisplayAuthError(RuntimeError):
    """XAUTHORITY could not be resolved or authenticated against the display."""


def display_digit(display: str) -> str:
    """Strip leading colon / screen from ``:2`` / ``:2.0`` / ``2`` → ``2``."""
    raw = str(display or "").strip()
    if raw.startswith(":"):
        raw = raw[1:]
    number = raw.split(".", 1)[0].strip()
    if not number or not number.isdigit():
        raise DisplayAuthError(f"invalid X display {display!r}")
    return number


def per_display_auth_path(display: str, *, home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".gateway" / "cdp-xvfb" / display_digit(display) / "Xauthority"


def flat_auth_path(*, home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".gateway" / "cdp-xvfb" / "Xauthority"


def x11_socket_path(display: str) -> Path:
    return Path("/tmp/.X11-unix") / f"X{display_digit(display)}"


def discover_live_xvfb_auth(display: str) -> Path | None:
    """Parse running Xvfb cmdline for this display's ``-auth`` path."""
    digit = display_digit(display)
    display_tok = f":{digit}"
    try:
        proc = subprocess.run(
            ["pgrep", "-a", "-x", "Xvfb"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_XDPYINFO_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode not in (0, 1):
        return None
    for line in (proc.stdout or "").splitlines():
        # "12345 Xvfb :2 -auth /path ..."
        parts = line.split(None, 1)
        cmd = parts[1] if len(parts) == 2 else line
        if display_tok not in cmd.split():
            continue
        match = _XVFB_AUTH_RE.search(cmd)
        if match:
            path = Path(match.group(1))
            if path.is_file():
                return path
    return None


def resolve_display_auth(
    display: str,
    *,
    home: Path | None = None,
    discover: bool = True,
) -> DisplayAuth:
    """per-display → flat → live Xvfb ``-auth``; never invent a silent inherit."""
    per = per_display_auth_path(display, home=home)
    if per.is_file():
        return DisplayAuth(path=per, source="per_display", required=True)
    flat = flat_auth_path(home=home)
    if flat.is_file():
        return DisplayAuth(path=flat, source="flat", required=True)
    if discover:
        live = discover_live_xvfb_auth(display)
        if live is not None:
            return DisplayAuth(path=live, source="live_xvfb", required=True)
    return DisplayAuth(path=None, source="none", required=True)


def apply_display_auth_env(
    env: dict[str, str],
    display: str,
    *,
    auth: DisplayAuth | None = None,
    home: Path | None = None,
) -> DisplayAuth:
    """Bind DISPLAY + XAUTHORITY on *env*; strip inherited XAUTHORITY first."""
    resolved = auth if auth is not None else resolve_display_auth(display, home=home)
    env["DISPLAY"] = display
    env["CDP_DISPLAY"] = display
    env.pop("XAUTHORITY", None)
    if resolved.path is not None:
        env["XAUTHORITY"] = str(resolved.path)
    elif resolved.required:
        raise DisplayAuthError(
            f"CDP display {display} has no resolvable Xauthority "
            f"(tried per-display, flat, live Xvfb -auth)"
        )
    return resolved


def xdpyinfo_status(
    display: str,
    *,
    env: Mapping[str, str] | None = None,
    timeout_s: float = _XDPYINFO_TIMEOUT_S,
) -> tuple[str, str]:
    """Return ``(live|unauthorized|dead|hung, stderr_snippet)``.

    Uses socket presence + xdpyinfo stderr; never blocks longer than *timeout_s*.
    """
    socket_live = x11_socket_path(display).exists()
    run_env = dict(env) if env is not None else {**os.environ, "DISPLAY": display}
    run_env.setdefault("DISPLAY", display)
    try:
        proc = subprocess.run(
            ["xdpyinfo", "-display", display],
            capture_output=True,
            check=False,
            env=run_env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return "hung", "xdpyinfo timed out"
    err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
    snippet = " ".join(err.split())[:200]
    if proc.returncode == 0:
        return "live", snippet
    lower = err.lower()
    if (
        "authorization required" in lower
        or "no protocol specified" in lower
        or "invalid MIT-MAGIC-COOKIE" in lower
    ):
        return "unauthorized", snippet
    if not socket_live:
        return "dead", snippet or "no X11 socket"
    return "dead", snippet or "xdpyinfo failed"


def require_auth_authenticates(
    display: str,
    *,
    env: Mapping[str, str],
) -> None:
    """Probe that *env*'s XAUTHORITY actually opens *display* (timeout-bounded)."""
    status, snippet = xdpyinfo_status(display, env=env)
    if status == "live":
        return
    raise DisplayAuthError(
        f"CDP display {display} {status} via "
        f"XAUTHORITY={env.get('XAUTHORITY', '<unset>')}: {snippet or status}"
    )
