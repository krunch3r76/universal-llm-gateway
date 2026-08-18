"""Fail closed when a pytest process would open a production ~/.gateway ledger.

Git-integration-worker stores (dispatch ledger, seat-write ledger) resolve
``DATA_DIR`` with a ``~/.gateway`` fallback. A test that forgets the session
fixture otherwise writes fixture rows into the live databases. Callers are the
per-ledger ``_ledger_path`` functions; this module holds the shared predicate
so a new store cannot get the fallback without the belt.
"""

from __future__ import annotations

import os
from pathlib import Path

_FIXTURE_HINT = (
    "Set DATA_DIR to a tmp path (see services/git_integration_worker/"
    "tests/conftest.py::_isolate_dispatch_ledger)."
)


def refuse_live_ledger_under_pytest(data_dir: Path, *, ledger_label: str) -> None:
    """Raise when ``data_dir`` resolves inside ``~/.gateway`` during pytest.

    No-op outside pytest (``PYTEST_CURRENT_TEST`` unset) and when ``data_dir``
    cannot be resolved. ``ledger_label`` is interpolated into the error so the
    failing store is identifiable (``dispatch ledger`` vs ``seat-write ledger``).
    """
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    live = (Path.home() / ".gateway").resolve()
    try:
        resolved = data_dir.resolve()
    except OSError:
        return
    if resolved == live or resolved.is_relative_to(live):
        raise RuntimeError(
            f"refusing to open the live {ledger_label} under pytest: {resolved}. "
            f"{_FIXTURE_HINT}"
        )
