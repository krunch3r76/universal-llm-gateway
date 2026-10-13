"""Capture pytest, ruff, docstring-quality, and compileall into one JSON manifest.

Autonomous arcs need durable verification artifacts — not dropped non_file shell
entries — so unattended trust rests on machine-readable proof, not a single manual
re-run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    stdout_tail: str
    stderr_tail: str = ""


@dataclass
class VerificationManifest:
    schema_version: int = 1
    captured_at: str = ""
    python: str = ""
    pytest: CommandResult | None = None
    ruff: CommandResult | None = None
    docstring: CommandResult | None = None
    compileall: CommandResult | None = None
    paths: list[str] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(cmd: list[str], *, cwd: Path | None = None) -> CommandResult:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(
        command=cmd,
        exit_code=proc.returncode,
        stdout_tail="\n".join(proc.stdout.splitlines()[-20:]),
        stderr_tail="\n".join(proc.stderr.splitlines()[-10:]),
    )


def capture_manifest(
    *,
    paths: list[str | Path],
    pytest_target: str | Path,
    workspace_root: Path | None = None,
    python: str | None = None,
) -> VerificationManifest:
    """Run the standard charter-runner verification stack on ``paths``."""
    py = python or sys.executable
    root = workspace_root or Path.cwd()
    norm_paths = [str(Path(p)) for p in paths]
    manifest = VerificationManifest(
        captured_at=datetime.now(UTC).isoformat(),
        python=py,
        paths=norm_paths,
    )
    manifest.pytest = _run(
        [py, "-m", "pytest", str(pytest_target), "-q", "-p", "no:cacheprovider"],
        cwd=root,
    )
    manifest.ruff = _run(
        [py, "-m", "ruff", "check", *norm_paths],
        cwd=root,
    )
    manifest.docstring = _run(
        [
            str(root / "scripts" / "docstring-quality"),
            "scan",
            *norm_paths,
        ],
        cwd=root,
    )
    manifest.compileall = _run(
        [py, "-m", "compileall", "-q", *norm_paths],
        cwd=root,
    )
    manifest.passed = all(
        r.exit_code == 0
        for r in (
            manifest.pytest,
            manifest.ruff,
            manifest.docstring,
            manifest.compileall,
        )
        if r is not None
    )
    return manifest


def write_manifest(manifest: VerificationManifest, dest: Path) -> Path:
    """Write ``manifest`` JSON to ``dest``; return the path written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")
    return dest
