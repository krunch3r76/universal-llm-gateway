"""Offline tests for cdp-lane-ensure repo resolution (installed symlink shape)."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ENSURE = _REPO / "services" / "jupiter-cdp" / "cdp-lane-ensure"
_PINS = _REPO / "services" / "jupiter-cdp" / "pins.toml"

_REPO_LINE_RE = re.compile(r'^REPO="\$\{ULG_REPO:-.*\}"$', re.MULTILINE)


def _repo_line_from_ensure() -> str:
    """Extract the live REPO= line from the real cdp-lane-ensure script."""
    text = _ENSURE.read_text()
    match = _REPO_LINE_RE.search(text)
    if not match:
        raise AssertionError("REPO= line not found in cdp-lane-ensure")
    return match.group(0)


def _sh_resolve_repo(argv0: str, *, env: dict[str, str] | None = None) -> Path:
    """Run the script's REPO= line with *argv0* as $0 (symlink shape)."""
    repo_line = _repo_line_from_ensure()
    proc = subprocess.run(
        ["sh", "-c", f"{repo_line}\necho \"$REPO\"", argv0],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(proc.stdout.strip())


def _env_without_ulg_repo() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "ULG_REPO"}


@pytest.mark.offline
def test_cdp_lane_ensure_repo_resolves_via_symlink_argv0(tmp_path: Path) -> None:
    """Simulate ~/.local/bin/cdp-lane-ensure symlink with ULG_REPO unset."""
    local_bin = tmp_path / "bin"
    local_bin.mkdir()
    link = local_bin / "cdp-lane-ensure"
    link.symlink_to(_ENSURE)

    assert _sh_resolve_repo(str(link), env=_env_without_ulg_repo()) == _REPO


@pytest.mark.offline
def test_cdp_lane_ensure_pins_path_and_lane_parse_via_symlink(tmp_path: Path) -> None:
    """Pins path from symlink-resolved REPO must exist and parse fleet lane."""
    local_bin = tmp_path / "bin"
    local_bin.mkdir()
    link = local_bin / "cdp-lane-ensure"
    link.symlink_to(_ENSURE)

    repo = _sh_resolve_repo(str(link), env=_env_without_ulg_repo())
    pins = repo / "services" / "jupiter-cdp" / "pins.toml"
    assert pins == _PINS

    with pins.open("rb") as f:
        lanes = tomllib.load(f)["lanes"]
    assert "fleet" in lanes
    assert lanes["fleet"]["port"] == 9222


@pytest.mark.offline
def test_doordash_alias_resolves_to_gopuff_unit() -> None:
    """doordash starts the existing 9270 unit; a broken alias target is invalid.

    Two CLI names must not create a second Chrome on the same port. install.sh
    only walks ``lanes``, so the alias table does not grow a systemd unit.
    """
    with _PINS.open("rb") as f:
        doc = tomllib.load(f)
    lanes = doc["lanes"]
    aliases = doc["lane_aliases"]
    assert aliases["doordash"] == "gopuff"
    assert aliases["doordash"] in lanes
    assert lanes["gopuff"]["port"] == 9270
    assert "doordash" not in lanes

    # Resolve only. Do not exec the script: it would systemctl-start the unit.
    proc = subprocess.run(
        [
            "python3",
            "-c",
            "import pathlib,tomllib,sys;"
            "doc=tomllib.loads(pathlib.Path(sys.argv[1]).read_text());"
            "lane=sys.argv[2];"
            "resolved=str(doc.get('lane_aliases',{}).get(lane,lane));"
            "assert resolved in doc['lanes'];"
            "print(resolved)",
            str(_PINS),
            "doordash",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "gopuff"
    assert "systemctl --user start \"cdp-lane@${LANE}.service\"" in _ENSURE.read_text()


@pytest.mark.offline
def test_alias_target_missing_is_invalid(tmp_path: Path) -> None:
    pins = tmp_path / "pins.toml"
    pins.write_text(
        '[lanes.fleet]\nport = 9222\ndisplay = ":2"\nprofile = "~/p"\n'
        'standing = true\nlapsed_url_prefixes = []\nextra_args = []\n'
        '[lane_aliases]\ndoordash = "missing"\n',
        encoding="utf-8",
    )
    script = tmp_path / "ensure.sh"
    text = _ENSURE.read_text(encoding="utf-8")
    text = text.replace(
        'PINS="$REPO/services/jupiter-cdp/pins.toml"',
        f'PINS="{pins}"',
    )
    script.write_text(text, encoding="utf-8")
    script.chmod(0o755)
    proc = subprocess.run(
        ["sh", str(script), "doordash"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "invalid lane: doordash" in proc.stderr


@pytest.mark.offline
def test_installed_wrapper_embeds_ulg_repo() -> None:
    """install.sh wrapper pattern exports ULG_REPO before exec (AC-7)."""
    install_sh = (_REPO / "services" / "jupiter-cdp" / "install.sh").read_text()
    assert 'cat >"$LOCAL_BIN/cdp-lane-ensure"' in install_sh
    assert "ULG_REPO=$REPO" in install_sh
    assert 'exec "$REPO/services/jupiter-cdp/cdp-lane-ensure"' in install_sh
    assert 'rm -f \\' in install_sh or 'rm -f "$LOCAL_BIN/cdp-lane-ensure"' in install_sh
