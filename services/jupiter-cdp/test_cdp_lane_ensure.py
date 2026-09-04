"""Offline tests for cdp-lane-ensure repo resolution (installed symlink shape)."""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ENSURE = _REPO / "services" / "jupiter-cdp" / "cdp-lane-ensure"
_PINS = _REPO / "services" / "jupiter-cdp" / "pins.toml"

_RESOLVE_REPO = r"""
REPO="${ULG_REPO:-$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)}"
echo "$REPO"
"""

_VALIDATE_FLEET = r"""
REPO="${ULG_REPO:-$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)}"
PINS="$REPO/services/jupiter-cdp/pins.toml"
python3 - "$PINS" fleet <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    lanes = tomllib.load(f).get("lanes", {})
sys.exit(0 if "fleet" in lanes else 1)
PY
"""


def _env_without_ulg_repo() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "ULG_REPO"}


@pytest.mark.offline
def test_cdp_lane_ensure_repo_resolves_via_symlink_argv0(tmp_path: Path) -> None:
    """Simulate ~/.local/bin/cdp-lane-ensure symlink with ULG_REPO unset."""
    local_bin = tmp_path / "bin"
    local_bin.mkdir()
    link = local_bin / "cdp-lane-ensure"
    link.symlink_to(_ENSURE)

    proc = subprocess.run(
        ["sh", "-c", _RESOLVE_REPO, str(link)],
        env=_env_without_ulg_repo(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(proc.stdout.strip()) == _REPO


@pytest.mark.offline
def test_cdp_lane_ensure_pins_path_and_lane_parse_via_symlink(tmp_path: Path) -> None:
    """Pins path from symlink-resolved REPO must exist and parse fleet lane."""
    local_bin = tmp_path / "bin"
    local_bin.mkdir()
    link = local_bin / "cdp-lane-ensure"
    link.symlink_to(_ENSURE)

    subprocess.run(
        ["sh", "-c", _VALIDATE_FLEET, str(link)],
        env=_env_without_ulg_repo(),
        check=True,
    )

    with _PINS.open("rb") as f:
        lanes = tomllib.load(f)["lanes"]
    assert lanes["fleet"]["port"] == 9222


@pytest.mark.offline
def test_installed_wrapper_embeds_ulg_repo() -> None:
    """install.sh wrapper pattern exports ULG_REPO before exec (AC-7)."""
    install_sh = (_REPO / "services" / "jupiter-cdp" / "install.sh").read_text()
    assert 'cat >"$LOCAL_BIN/cdp-lane-ensure"' in install_sh
    assert "ULG_REPO=$REPO" in install_sh
    assert 'exec "$REPO/services/jupiter-cdp/cdp-lane-ensure"' in install_sh
