"""Skill-catalog census↔yaml parity gate for quality_gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

_TIMEOUT = 30

_TOUCH_MARKERS: tuple[str, ...] = (
    "cursor-plugins/ulg-ecosystem/SKILLS_CENSUS.txt",
    "config/skills.yaml",
    ".cursor/skills/",
    "libs/claude_bundles/catalog.py",
    "scripts/cortex/validate_skill_catalog.py",
    "scripts/hooks/validate_skill_catalog_staged.py",
)


def paths_touch_skill_catalog(files: list[str]) -> bool:
    """True when any path can drift census↔config/skills.yaml parity."""
    for path in files:
        for marker in _TOUCH_MARKERS:
            if marker in path or path.endswith(marker) or path == marker:
                return True
            if path.startswith(marker):
                return True
    return False


def run_skill_catalog_gate(
    files: list[str],
    *,
    repo_root: Path,
    timeout: int = _TIMEOUT,
) -> dict[str, Any]:
    """Fail-closed when touched files implicate skill-catalog SOT parity."""
    if not paths_touch_skill_catalog(files):
        return {
            "passed": True,
            "skipped": True,
            "output": "no skill-catalog SOT files; parity check skipped",
        }

    validator = repo_root / "scripts" / "cortex" / "validate_skill_catalog.py"
    if not validator.is_file():
        return {
            "passed": False,
            "output": f"skill-catalog validator missing: {validator}",
        }

    try:
        result = subprocess.run(
            [sys.executable, str(validator), "--root", str(repo_root)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo_root),
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "skill-catalog parity check timed out"}

    output = (result.stderr + result.stdout).strip()
    return {
        "passed": result.returncode == 0,
        "output": output[:4000] if output else "(no output)",
    }
