"""Ensure the shared virtual environment exists for onboarding.

If $HOME/.venvs/universal is missing, creates it, installs requirements.txt,
copies sitecustomize.py for libs/ path setup, and re-execs the TUI so the
session runs with that environment. UI-agnostic; uses stdlib only so it can
be reused by CLI or scripts.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

VENV_DIR = Path.home() / ".venvs" / "universal"
VENV_BIN_PYTHON = "bin/python"
REQUIREMENTS_FILE = "requirements.host.txt"
SITECUSTOMIZE = "sitecustomize.py"


def find_workspace_root() -> Path:
    """Walk up from CWD to find the repo root (has pyproject.toml + config/)."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists() and (parent / "config").is_dir():
            return parent
    print("Error: Cannot find workspace root.", file=sys.stderr)
    print("Run from within the universal-llm-gateway repository.", file=sys.stderr)
    sys.exit(1)


def _install_sitecustomize(workspace_root: Path, venv_dir: Path) -> None:
    """Copy sitecustomize.py into the venv's site-packages for libs/ path setup."""
    source = workspace_root / SITECUSTOMIZE
    if not source.exists():
        print("Warning: sitecustomize.py not found at", source, file=sys.stderr)
        return
    lib_dir = venv_dir / "lib"
    candidates = list(lib_dir.glob("python*/site-packages"))
    if not candidates:
        print("Warning: no site-packages found in", lib_dir, file=sys.stderr)
        return
    dest = candidates[0] / SITECUSTOMIZE
    shutil.copy2(source, dest)


def ensure_venv(workspace_root: Path) -> None:
    """Ensure $HOME/.venvs/universal exists; create and install if missing, then re-exec.

    If the venv's bin/python exists, returns and the caller continues.
    If not, creates the venv, runs pip install -r requirements.txt, copies
    sitecustomize.py, then re-execs with that Python (so this process is
    replaced). On any failure, prints to stderr and exits with code 1.
    """
    venv_python = VENV_DIR / VENV_BIN_PYTHON
    if venv_python.exists():
        return

    VENV_DIR.parent.mkdir(parents=True, exist_ok=True)

    create_cmd = [sys.executable, "-m", "venv", str(VENV_DIR)]
    print("Creating venv at", VENV_DIR, file=sys.stderr)
    result = subprocess.run(create_cmd, cwd=workspace_root)
    if result.returncode != 0:
        print("Error: venv creation failed.", file=sys.stderr)
        sys.exit(1)

    _install_sitecustomize(workspace_root, VENV_DIR)

    requirements = workspace_root / REQUIREMENTS_FILE
    if not requirements.exists():
        print("Error: Requirements file not found:", requirements, file=sys.stderr)
        sys.exit(1)

    pip_exe = venv_python.parent / "pip"
    pip_cmd = [str(pip_exe), "install", "-r", str(requirements)]
    print("Installing requirements from", requirements, file=sys.stderr)
    result = subprocess.run(pip_cmd, cwd=workspace_root)
    if result.returncode != 0:
        print("Error: pip install -r requirements.txt failed.", file=sys.stderr)
        sys.exit(1)

    argv = [str(venv_python), "-m", "scripts.model_manager.ui", *sys.argv[1:]]
    os.execv(str(venv_python), argv)
