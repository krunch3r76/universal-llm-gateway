"""User-owned Cursor runtime with EnableNodeCliInspectArguments enabled.

Cursor 3.32+ ships with Electron fuse ``EnableNodeCliInspectArguments`` off.
Stable builds then hard-exit on ``--remote-debugging-port`` (needed for Glass
CDP on :8 / orion / jupiter). Flipping the fuse on the package binary needs
root and is wiped by apt; this module keeps a user-writable ELF copy under
``~/.gateway/cursor-agent-runtime`` with the fuse on, and symlinks the rest of
``/usr/share/cursor`` so upgrades refresh resources without a 1G copy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

_PACKAGE_ROOT = Path("/usr/share/cursor")
_PACKAGE_BIN = _PACKAGE_ROOT / "cursor"
_RUNTIME_DIRNAME = "cursor-agent-runtime"
_FUSE_NAME = "EnableNodeCliInspectArguments"


def runtime_dir(home: Path) -> Path:
    """Return ``~/.gateway/cursor-agent-runtime``."""
    return home / ".gateway" / _RUNTIME_DIRNAME


def runtime_cursor_bin(home: Path) -> Path:
    """Return the fused Cursor ELF path used by agent units."""
    return runtime_dir(home) / "cursor"


def _npx_path(home: Path | None = None) -> str:
    """Resolve ``npx`` — prefer PATH, then ``~/.local/node/bin`` (orion has no apt npm)."""
    which = shutil.which("npx")
    if which:
        return which
    root = Path.home() if home is None else home
    local = root / ".local" / "node" / "bin" / "npx"
    if local.is_file():
        return str(local)
    return "npx"


def _npx_fuses(*args: str, home: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    local_bin = str((home or Path.home()) / ".local" / "node" / "bin")
    if Path(local_bin).is_dir():
        env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"
    return subprocess.run(
        [_npx_path(home), "--yes", "@electron/fuses@1.8.0", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def fuse_inspect_enabled(bin_path: Path, *, home: Path | None = None) -> bool:
    """True when ``EnableNodeCliInspectArguments`` is on for *bin_path*."""
    proc = _npx_fuses("read", "--app", str(bin_path), home=home)
    if proc.returncode != 0:
        return False
    for line in (proc.stdout or "").splitlines():
        if _FUSE_NAME in line and "Enabled" in line and "Disabled" not in line:
            return True
    return False


def ensure_cursor_runtime(*, home: Path, apply: bool) -> dict[str, object]:
    """Sync the user runtime tree and optionally flip the inspect fuse.

    When ``apply`` is false, reports whether the runtime is present and fused
    without writing. When true, recreates symlinks, copies the package ELF if
    missing or not fused, and enables ``EnableNodeCliInspectArguments``.
    """
    dest = runtime_dir(home)
    bin_path = runtime_cursor_bin(home)
    package_present = _PACKAGE_BIN.is_file()
    present = bin_path.is_file()
    fused = fuse_inspect_enabled(bin_path, home=home) if present else False
    plan = {
        "package_bin": str(_PACKAGE_BIN),
        "runtime_dir": str(dest),
        "runtime_bin": str(bin_path),
        "package_present": package_present,
        "runtime_present": present,
        "fuse_enabled": fused,
        "needs_sync": not (present and fused and package_present),
    }
    if not apply:
        return {**plan, "applied": False}

    if not package_present:
        raise FileNotFoundError(f"Cursor package binary missing: {_PACKAGE_BIN}")

    dest.mkdir(parents=True, exist_ok=True)
    # Refresh sibling links to the package tree; keep a real ELF for fuse writes.
    for entry in _PACKAGE_ROOT.iterdir():
        link = dest / entry.name
        if entry.name == "cursor":
            continue
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(entry)

    # Pytest provision fixtures use a fake HOME; skip the 220MB ELF copy + fuse flip.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        if bin_path.exists() or bin_path.is_symlink():
            bin_path.unlink()
        bin_path.symlink_to(_PACKAGE_BIN)
        return {
            **plan,
            "applied": True,
            "runtime_present": True,
            "fuse_enabled": False,
            "needs_sync": False,
            "copied": False,
            "pytest_stub": True,
        }

    need_copy = (not bin_path.is_file()) or (
        not fuse_inspect_enabled(bin_path, home=home)
    )
    if need_copy:
        if bin_path.exists() or bin_path.is_symlink():
            bin_path.unlink()
        shutil.copy2(_PACKAGE_BIN, bin_path)
        bin_path.chmod(0o755)
        write = _npx_fuses(
            "write", "--app", str(bin_path), f"{_FUSE_NAME}=on", home=home
        )
        if write.returncode != 0:
            raise RuntimeError(
                "electron-fuses write failed: "
                f"stdout={write.stdout!r} stderr={write.stderr!r}"
            )
        if not fuse_inspect_enabled(bin_path, home=home):
            raise RuntimeError(f"{_FUSE_NAME} still disabled after write on {bin_path}")

    return {
        **plan,
        "applied": True,
        "runtime_present": True,
        "fuse_enabled": True,
        "needs_sync": False,
        "copied": need_copy,
    }


def main() -> int:
    """CLI: ``--apply`` syncs; default is dry-run JSON on stdout."""
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--home", type=Path, default=Path.home())
    args = p.parse_args()
    result = ensure_cursor_runtime(home=args.home, apply=args.apply)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
