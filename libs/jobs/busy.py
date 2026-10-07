"""Read open jobs folds from journal.db for the restart drain.

The manage busy probe calls ``open_run_rows`` and does not add a seventh
HTTP route. A ``bus-reply-watch`` row in admitted, running, or cancelling
is enough to defer ``sync_restart``. Delivery rows are not open work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jobs.journal import Journal, journal_path


def open_run_rows(path: Path | None = None) -> list[dict[str, Any]]:
    """Return open folds from the jobs journal, or an empty list if it is absent.

    Missing database means the satellite has never admitted a run, which is
    idle. A corrupt file propagates so the drain can fail closed.
    """
    db = path or journal_path()
    if not db.is_file():
        return []
    return Journal(db).open_runs()
