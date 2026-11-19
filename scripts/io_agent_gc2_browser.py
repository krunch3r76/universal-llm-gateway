"""Install gc2 (:10) browser / xdg-open wrappers for Grok Bot OAuth on io."""

from __future__ import annotations

from grokbot_box_mcp.gc2_browser import (
    ensure_gc2_browser_shims,
    gc2_service_environment as _gc2_service_environment,
)


def provision_gc2_browser_config(*, home=None) -> dict[str, object]:
    """Write gc2 browser wrappers and desktop entries under ``~/.config/io-agent-gc2``."""
    from pathlib import Path

    return ensure_gc2_browser_shims(home=Path(home) if home else None)


def gc2_service_environment(*, home=None) -> dict[str, str]:
    """systemd Environment= keys for io-agent-grok-gc2 OAuth browser routing."""
    from pathlib import Path

    return _gc2_service_environment(home=Path(home) if home else None)


def main() -> int:
    out = ensure_gc2_browser_shims()
    if not out.get("chromium_ready"):
        print(
            "ensure: shims synced; Playwright Chromium not found — "
            "run: $HOME/.venvs/universal/bin/playwright install chromium",
            flush=True,
        )
    else:
        print(f"ensure: ok chromium={out['chromium_bin']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())