from __future__ import annotations

import re
from pathlib import Path

_MIGRATION_PY = re.compile(r"^\d{3}_.+\.py$")
_MIGRATIONS_DIR = (Path(__file__).resolve().parent / "migrations").resolve()


def migration_filename_from_filenotfound(exc: BaseException) -> str | None:
    """Return the missing numbered migration filename, or None.

    Matches only FileNotFoundError for libs/cortex_store/migrations/NNN_*.py.
    Any other missing path stays a collection error.
    """
    if not isinstance(exc, FileNotFoundError):
        return None
    raw = exc.filename
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not _MIGRATION_PY.match(path.name):
        return None
    if path.parent.resolve() != _MIGRATIONS_DIR:
        return None
    return path.name
