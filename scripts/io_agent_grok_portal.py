"""Per-display xdg-desktop-portal + session bus for Grok Bot Xvfb houses on io."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

from grokbot_box_mcp.window_units import GrokWindowProfile, load_window_profile


def portal_config_dir(home: Path, display_num: str) -> Path:
    return home / ".config" / f"io-agent-portal-{display_num}"


def _portal_run_sh_from_template(
    template_text: str,
    *,
    from_display: str,
    to_display: str,
    portal_config: Path,
) -> str:
    text = template_text
    text = text.replace(f"bus-display-{from_display}", f"bus-display-{to_display}")
    text = text.replace(f":{from_display}", f":{to_display}")
    home = str(Path.home())
    text = text.replace(
        f"{home}/.config/io-agent-portal-{from_display}",
        str(portal_config),
    )
    return text


def portal_unit_body(profile: GrokWindowProfile, *, home: Path) -> str:
    display_num = profile.display_num
    portal_config = portal_config_dir(home, display_num)
    import os

    uid = str(os.getuid())
    return f"""[Unit]
Description=File-chooser portal for agent display {profile.display} (Grok {profile.slot})
After={profile.xvfb_unit}
Requires={profile.xvfb_unit}

[Service]
Type=simple
Environment=DISPLAY={profile.display}
Environment=XDG_RUNTIME_DIR=/run/user/{uid}
Environment=XDG_CURRENT_DESKTOP=gnome
Environment=XDG_CONFIG_HOME={portal_config}
ExecStart={portal_config}/run.sh
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def _install_executable(path: Path) -> None:
    mode = path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    path.chmod(mode)


def provision_portal_for_profile(
    profile: GrokWindowProfile,
    *,
    home: Path | None = None,
    template_display: str = "10",
) -> dict[str, object]:
    """Seed portal config dir and write the matching systemd user unit."""
    home = home or Path.home()
    if profile.slot == "gw1":
        return {"ok": True, "skipped": True, "reason": "gw1 portal is host-managed"}
    display_num = profile.display_num
    dest = portal_config_dir(home, display_num)
    template = portal_config_dir(home, template_display)
    dest.mkdir(parents=True, exist_ok=True)
    if template.is_dir() and template != dest:
        for name in dest.iterdir():
            if name.name == "run.sh":
                continue
        for child in template.iterdir():
            if child.name == "run.sh":
                continue
            target = dest / child.name
            if child.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
    template_run = template / "run.sh"
    run_sh = dest / "run.sh"
    if template_run.is_file():
        run_sh.write_text(
            _portal_run_sh_from_template(
                template_run.read_text(encoding="utf-8"),
                from_display=template_display,
                to_display=display_num,
                portal_config=dest,
            ),
            encoding="utf-8",
        )
    else:
        raise FileNotFoundError(f"portal template missing run.sh: {template_run}")
    _install_executable(run_sh)
    unit_name = f"io-agent-portal-{display_num}.service"
    unit_body = portal_unit_body(profile, home=home)
    unit_path = home / ".config" / "systemd" / "user" / unit_name
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_body, encoding="utf-8")
    return {
        "ok": True,
        "portal_config": str(dest),
        "portal_unit": str(unit_path),
        "portal_unit_name": unit_name,
    }


def provision_portal_for_slot(key: str, *, home: Path | None = None) -> dict[str, object]:
    home = home or Path.home()
    profile = load_window_profile(key, home=home)
    return provision_portal_for_profile(profile, home=home)
