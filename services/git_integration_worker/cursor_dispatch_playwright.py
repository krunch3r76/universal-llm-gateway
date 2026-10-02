"""Playwright browser cache for cursor-sdk dispatch HOMEs (a:37291).

Playwright defaults to ``$HOME/.cache/ms-playwright``. GIW swaps HOME to an
empty overlay, so ``chromium.launch`` fails with the stock ``playwright
install`` banner even though the operator cache is present. Seed a pointer
under the dispatch home and export the same path for the bridge env shim so
resume of pre-seed homes still finds chromium.
"""

from __future__ import annotations

from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

PLAYWRIGHT_BROWSERS_RELPATH = Path(".cache") / "ms-playwright"


def operator_playwright_browsers_dir(
    real_home: Path | str | None,
) -> Path | None:
    """Return the operator Playwright browser cache when present, else ``None``."""
    if real_home is None:
        return None
    src = Path(real_home).expanduser() / PLAYWRIGHT_BROWSERS_RELPATH
    return src if src.is_dir() else None


def link_operator_playwright_browsers(home: Path, real: Path) -> None:
    """Point dispatch ``$HOME/.cache/ms-playwright`` at the operator cache.

    Pointer only — never copy the browser tree. No-op when the operator cache
    is absent or ``dst`` already exists as a non-matching path.
    """
    src = operator_playwright_browsers_dir(real)
    if src is None:
        return
    dst = home / PLAYWRIGHT_BROWSERS_RELPATH
    if dst.is_symlink():
        try:
            if dst.resolve() == src.resolve():
                return
        except OSError:
            pass
        logger.warning(
            "dispatch_home: playwright-browsers pointer %s exists and does not "
            "match %s",
            dst,
            src,
        )
        return
    if dst.exists():
        logger.warning(
            "dispatch_home: playwright-browsers pointer skipped; %s already exists",
            dst,
        )
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(src, target_is_directory=True)
    except OSError as exc:
        logger.warning(
            "dispatch_home: playwright-browsers pointer skipped %s -> %s: %s",
            dst,
            src,
            exc,
        )
