"""Offline checks for install-ecosystem-plugin skill directory staging."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_INSTALL = _REPO / "scripts" / "cursor" / "install-ecosystem-plugin.sh"
_REASONING = _REPO / "cursor-plugins" / "ulg-ecosystem" / "skills" / "reasoning-posture"
_CONDUCTOR = _REPO / "cursor-plugins" / "ulg-ecosystem" / "skills" / "conductor"
_CORTEX_ORIENTATION = (
    _REPO / "cursor-plugins" / "ulg-ecosystem" / "skills" / "cortex-orientation"
)


def _passwd_home() -> str:
    import pwd

    return pwd.getpwnam(os.environ.get("USER", os.getlogin())).pw_dir


@pytest.mark.offline
def test_reasoning_posture_reference_companion_present() -> None:
    skill = _REASONING / "SKILL.md"
    ref = _REASONING / "reference.md"
    assert skill.is_file()
    assert ref.is_file()
    body = skill.read_text(encoding="utf-8")
    assert "## Six rules" not in body
    assert "reference.md beside this file" in body


@pytest.mark.offline
def test_conductor_reference_siblings_present_in_sot() -> None:
    refs = list(_CONDUCTOR.glob("reference-*.md"))
    assert len(refs) >= 5


@pytest.mark.offline
def test_cortex_orientation_reference_companion_present_in_sot() -> None:
    skill = _CORTEX_ORIENTATION / "SKILL.md"
    ref = _CORTEX_ORIENTATION / "reference.md"
    assert skill.is_file()
    assert ref.is_file()
    assert not (_CORTEX_ORIENTATION / "references").exists()
    body = skill.read_text(encoding="utf-8")
    assert "references/details.md" not in body
    assert "reference.md" in body


@pytest.mark.offline
def test_cortex_orientation_reference_staged_on_install_dry_run() -> None:
    # Installer registers EXIT trap that rm -rf staging; assert while bash still runs.
    env = {**os.environ, "HOME": _passwd_home()}
    proc = subprocess.Popen(
        ["bash", str(_INSTALL), "--dry-run"],
        cwd=str(_REPO),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None
    saw_staging = False
    for line in proc.stdout:
        match = re.search(r"staging at (\S+)", line)
        if not match:
            continue
        staging = Path(match.group(1))
        staged_ref = staging / "skills" / "cortex-orientation" / "reference.md"
        assert staged_ref.is_file(), f"missing staged companion {staged_ref}"
        saw_staging = True
    rc = proc.wait()
    assert rc == 0
    assert saw_staging
