"""Parameterised Grok Bot window profiles and systemd unit bodies (io host)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GROK_WINDOWS = _REPO_ROOT / "config" / "grok-windows.json"


@dataclass(frozen=True)
class GrokWindowProfile:
    slot: str
    display: str
    cdp_port: int
    user_data_dir: Path
    browser_config_name: str  # io-agent-gc1 | io-agent-gc2

    @property
    def display_num(self) -> str:
        return self.display.lstrip(":")

    @property
    def xvfb_unit(self) -> str:
        return f"io-agent-xvfb-{self.display_num}.service"

    @property
    def vnc_unit(self) -> str:
        return f"io-agent-vnc-59{self.display_num}.service"

    @property
    def grok_unit(self) -> str:
        return (
            "io-agent-grok.service"
            if self.slot == "gc1"
            else f"io-agent-grok-{self.slot}.service"
        )

    @property
    def portal_unit(self) -> str | None:
        if self.display_num == "10":
            return "io-agent-portal-10.service"
        return None


def _expand_user_data(raw: str, home: Path) -> Path:
    if raw.startswith("~/"):
        return home / raw[2:]
    return Path(raw).expanduser()


def load_window_profile(
    key: str,
    *,
    home: Path | None = None,
) -> GrokWindowProfile:
    """Load gc1/gc2 (or display number ``9`` / ``10``) from config/grok-windows.json."""
    home = home or Path.home()
    data = json.loads(_GROK_WINDOWS.read_text(encoding="utf-8"))
    windows: dict[str, object] = data.get("windows", {})
    if key in {"gc1", "gc2"}:
        for display, entry in windows.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("slot") == key:
                return _profile_from_entry(key, display, entry, home)
        raise KeyError(f"slot {key!r} not in {_GROK_WINDOWS}")
    if key in windows:
        entry = windows[key]
        if not isinstance(entry, dict):
            raise KeyError(key)
        slot = str(entry.get("slot", key))
        return _profile_from_entry(slot, key, entry, home)
    raise KeyError(f"unknown grok window key {key!r}")


def _profile_from_entry(
    slot: str,
    display: str,
    entry: dict[str, object],
    home: Path,
) -> GrokWindowProfile:
    cdp = int(entry["cdp_port"])
    udd = _expand_user_data(str(entry["user_data_dir"]), home)
    browser = "io-agent-gc1" if slot == "gc1" else f"io-agent-{slot}"
    return GrokWindowProfile(
        slot=slot,
        display=f":{display}" if not str(display).startswith(":") else str(display),
        cdp_port=cdp,
        user_data_dir=udd,
        browser_config_name=browser,
    )


def xvfb_unit_body(profile: GrokWindowProfile, *, home: Path) -> str:
    return f"""[Unit]
Description=Agent Xvfb {profile.display} ({profile.slot}) 1920x1200
After=network.target

[Service]
EnvironmentFile={home}/.config/io-agent-display.env
ExecStart=/usr/bin/Xvfb {profile.display} -screen 0 ${{IO_AGENT_GEOMETRY}} -ac -nolisten tcp
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def vnc_unit_body(profile: GrokWindowProfile) -> str:
    port = f"59{profile.display_num}"
    return f"""[Unit]
Description=x11vnc for agent display {profile.display} (port {port})
After={profile.xvfb_unit}
Requires={profile.xvfb_unit}

[Service]
ExecStart=/usr/bin/x11vnc -display {profile.display} -localhost -no6 -rfbport {port} -nopw -shared -forever -noxdamage -skip_lockkeys -clear_all
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def grok_systemd_unit_body(
    profile: GrokWindowProfile,
    *,
    home: Path,
    env_block: str,
) -> str:
    after = [profile.xvfb_unit]
    requires = [profile.xvfb_unit]
    if profile.portal_unit:
        after.append(profile.portal_unit)
        requires.append(profile.portal_unit)
    after_s = " ".join(after)
    requires_s = " ".join(requires)
    udd = profile.user_data_dir
    maximize = home / ".config/io-agent-maximize.py"
    return f"""[Unit]
Description=Grok Bot {profile.slot} agent profile on display {profile.display} (CDP {profile.cdp_port})
After={after_s}
Requires={requires_s}

[Service]
{env_block}
ExecStart="/opt/Grok Bot/grok-bot" --user-data-dir={udd} --remote-debugging-port={profile.cdp_port} --window-size=1920,1200 --window-position=0,0
ExecStartPost={home}/.venvs/universal/bin/python {maximize} {profile.display}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""
