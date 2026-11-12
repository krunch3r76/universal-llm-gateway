"""Shared Grok Bot window browser env and systemd blocks (gc1 / gc2)."""

from __future__ import annotations

import os
from pathlib import Path

from grokbot_box_mcp.window_units import GrokWindowProfile, load_window_profile
from io_agent_gc2_browser import (
    format_systemd_environment_block,
    provision_gc2_browser_config,
)


def grok_service_environment(profile: GrokWindowProfile, *, home: Path | None = None) -> dict[str, str]:
    """Environment keys aligned with live io-agent-grok*.service units on io."""
    home = home or Path.home()
    udd = profile.user_data_dir
    if profile.slot == "gc2":
        config = home / ".config" / "io-agent-gc2"
        bin_dir = config / "bin"
        browser = bin_dir / "gc2-open-url.sh"
        env = {
            "DISPLAY": profile.display,
            "GROK_AGENT_DATA_DIR": str(udd),
            "XDG_CONFIG_HOME": str(udd / ".config"),
            "MOZ_DISABLE_REMOTE": "1",
            "PATH": os.pathsep.join([str(bin_dir), "/usr/local/bin", "/usr/bin", "/bin"]),
            "BROWSER": str(browser),
            "XDG_CONFIG_DIRS": f"{config}:/etc/xdg",
        }
        if profile.display_num == "10":
            uid = os.getuid()
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus-display-10"
        return env
    # gc1 — live io-agent-grok.service shape
    config = home / ".config" / "io-agent-gc1"
    bin_dir = config / "bin"
    browser = bin_dir / "gc1-open-url.sh"
    return {
        "DISPLAY": profile.display,
        "GROK_AGENT_DATA_DIR": str(udd),
        "XDG_CONFIG_HOME": str(udd / ".config"),
        "MOZ_DISABLE_REMOTE": "1",
        "PATH": os.pathsep.join([str(bin_dir), "/usr/local/bin", "/usr/bin", "/bin"]),
        "BROWSER": str(browser),
    }


def provision_browser_for_profile(profile: GrokWindowProfile, *, home: Path | None = None) -> dict[str, object]:
    if profile.slot == "gc2":
        return provision_gc2_browser_config(home=home)
    # gc1 uses the same open-url script pattern; gc1 wrapper may exist only on host today.
    return {"ok": True, "slot": profile.slot, "note": "gc1 browser config is host-managed (io-agent-gc1)"}


def systemd_grok_bundle(
    key: str,
    *,
    home: Path | None = None,
) -> dict[str, str]:
    """Return systemd unit name → body for Xvfb, VNC, and Grok for one window."""
    from grokbot_box_mcp.window_units import (
        grok_systemd_unit_body,
        vnc_unit_body,
        xvfb_unit_body,
    )

    home = home or Path.home()
    profile = load_window_profile(key, home=home)
    env_block = format_systemd_environment_block(grok_service_environment(profile, home=home))
    units = {
        profile.xvfb_unit: xvfb_unit_body(profile, home=home),
        profile.vnc_unit: vnc_unit_body(profile),
        profile.grok_unit: grok_systemd_unit_body(profile, home=home, env_block=env_block),
    }
    return units
