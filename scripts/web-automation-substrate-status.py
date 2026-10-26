#!/usr/bin/env python3
"""One-shot health check for web automation substrates on the io seat.

Checks Jupiter CDP tunnels (from ~/.gateway/cdp-tunnels.toml), Playwright browser
bundles, and prints the recommended automation pattern per lane class.

Usage:
  HOME=/home/io python scripts/web-automation-substrate-status.py
  python scripts/web-automation-substrate-status.py --json

After `pip install -U playwright` in ~/.venvs/universal:
  playwright install chromium firefox
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

# Lane → default local CDP URL (overridable via env, see web-automation-discipline).
_LANE_ENV = {
    "fleet": "FLEET_CDP_URL",
    "messages": "MESSAGES_CDP_URL",
    "ess": "ESS_CDP_URL",
    "gopuff": "DOORDASH_CDP_URL",
    "calendar": "CALENDAR_CDP_URL",
    "uber": "UBER_CDP_URL",
}


def _operator_home() -> Path:
    """Real io operator home — not cursor-sdk lane-B sandbox overlay."""
    for key in ("ULG_OPERATOR_HOME", "REAL_HOME"):
        raw = os.environ.get(key, "").strip()
        if raw:
            return Path(raw)
    home = Path.home()
    if (home / ".gateway" / "cdp-tunnels.toml").is_file():
        return home
    fallback = Path("/home/io")
    if (fallback / ".gateway" / "cdp-tunnels.toml").is_file():
        return fallback
    return home


def _tunnels_path() -> Path:
    return _operator_home() / ".gateway" / "cdp-tunnels.toml"


def _playwright_cache() -> Path:
    return _operator_home() / ".cache" / "ms-playwright"


def _playwright_cli() -> Path:
    return _operator_home() / ".venvs" / "universal" / "bin" / "playwright"


def _load_lanes() -> dict[str, dict[str, Any]]:
    tunnels = _tunnels_path()
    if not tunnels.is_file():
        return {}
    data = tomllib.loads(tunnels.read_text(encoding="utf-8"))
    lanes = data.get("lanes") or {}
    return {k: v for k, v in lanes.items() if isinstance(v, dict)}


def _probe_cdp(url: str, timeout: float = 4.0) -> dict[str, Any]:
    target = url.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(target, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        browser = str(body.get("Browser") or "")
        return {"ok": True, "url": url, "browser": browser}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "url": url, "error": str(exc)[:200]}


def _browser_executable(bundle_dir: Path, browser: str) -> str:
    if browser == "chromium":
        candidates = (
            bundle_dir / "chrome-linux64" / "chrome",
            bundle_dir / "chrome-linux" / "chrome",
            bundle_dir / "chrome" / "chrome",
        )
    else:
        candidates = (bundle_dir / "firefox" / "firefox",)
    for path in candidates:
        if path.is_file():
            return str(path)
    return str(bundle_dir)


def _playwright_bundles() -> dict[str, Any]:
    cache = _playwright_cache()
    found: dict[str, str] = {}
    if cache.is_dir():
        for name in ("chromium", "firefox"):
            matches = sorted(cache.glob(f"{name}-*"))
            if matches:
                found[name] = _browser_executable(matches[-1], name)
    cli = _playwright_cli()
    return {
        "cache_dir": str(cache),
        "bundles": found,
        "cli": str(cli) if cli.is_file() else None,
        "hint": (
            "Run: $HOME/.venvs/universal/bin/playwright install chromium firefox"
            if not found
            else None
        ),
    }


def _pattern_for_lane(lane: str) -> str:
    if lane in ("fleet", "messages", "ess", "gopuff", "calendar", "uber"):
        return (
            "Playwright async: connect_over_cdp(local_tunnel_url) — reattach live "
            "Chrome; do not launch() a new profile. Set HOME to operator home and "
            "PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright for any launch fallback."
        )
    return "see jupiter-browser-bringup"


def build_report() -> dict[str, Any]:
    lanes_cfg = _load_lanes()
    lanes_out: list[dict[str, Any]] = []
    for name, cfg in sorted(lanes_cfg.items()):
        local_port = int(cfg.get("local_port") or 0)
        url = f"http://127.0.0.1:{local_port}" if local_port else ""
        env_key = _LANE_ENV.get(name)
        if env_key and os.environ.get(env_key):
            url = os.environ[env_key].strip()
        probe = _probe_cdp(url) if url else {"ok": False, "error": "no local_port"}
        lanes_out.append(
            {
                "lane": name,
                "local_port": local_port,
                "cdp_url": url,
                "env_override": env_key,
                "standing": cfg.get("standing"),
                "purpose": cfg.get("purpose"),
                "cdp": probe,
                "automation_pattern": _pattern_for_lane(name),
            }
        )

    operator_home = str(_operator_home())
    return {
        "ok": all(row["cdp"].get("ok") for row in lanes_out if row.get("standing")),
        "operator_home": operator_home,
        "tunnels_config": str(_tunnels_path()),
        "lanes": lanes_out,
        "playwright": _playwright_bundles(),
        "attended_retail": {
            "surface": "io DISPLAY :11, VNC 5911",
            "browser": "system /usr/bin/firefox (not Playwright launch for bot-heavy PDPs)",
            "note": "Akamai retail: operator-attended; automation documents + opens tabs only",
        },
        "repair_hints": [
            "Jupiter lane down: services/jupiter-cdp/pin-go-live <lane> (on io repo path)",
            "Tunnel down: scripts.local/cdp/tunnel-ensure.sh ensure <lane> --remote",
            "Playwright out of sync: ~/.venvs/universal/bin/playwright install chromium firefox",
            "MCP browser_* (container Firefox): rebuild mcp-server image after pip bump "
            "(scripts/sync-and-restart-mcp.sh --no-cache); telemetry: mcp.browser.*",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report.get("ok") else 1

    print("Web automation substrate status\n")
    print(f"  operator_home: {report.get('operator_home')}")
    for row in report["lanes"]:
        mark = "OK" if row["cdp"].get("ok") else "FAIL"
        browser = row["cdp"].get("browser") or row["cdp"].get("error", "?")
        print(f"  [{mark}] {row['lane']:10} {row['cdp_url']:28} {browser}")
    pw = report["playwright"]
    print(f"\nPlaywright cache: {pw['cache_dir']}")
    for kind, path in pw.get("bundles", {}).items():
        print(f"  bundle {kind}: {path}")
    if pw.get("hint"):
        print(f"  → {pw['hint']}")
    print("\nAttended retail:", report["attended_retail"]["surface"])
    print("Repair:", " · ".join(report["repair_hints"][:2]))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
