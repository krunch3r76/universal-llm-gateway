"""Load the maestro-loop runbook from the cortex files root — read-only."""

from __future__ import annotations

from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root
from universal_logging import get_logger

_logger = get_logger(__name__)

_RUNBOOK_REL = Path("notes") / "runbooks" / "maestro-loop.md"


def load_maestro_runbook() -> tuple[str | None, str]:
    """Return ``(body, error_token)``. Success is ``(text, \"\")``."""
    root = cortex_files_root()
    if not root.is_dir():
        return None, "unreachable"
    path = root / _RUNBOOK_REL
    if not path.is_file():
        return None, "missing"
    try:
        return path.read_text(encoding="utf-8"), ""
    except OSError:
        _logger.exception("maestro runbook read failed: %s", path)
        return None, "unreachable"


__all__ = ["load_maestro_runbook"]
