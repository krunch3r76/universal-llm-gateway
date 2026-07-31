"""DB fixtures for implement-admission integration tests under tests/."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store import db
from cortex_store._test_db_bootstrap import copy_template_db


@pytest.fixture(scope="session")
def migrated_db_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from cortex_store._test_db_bootstrap import _session_template_path

    return _session_template_path(tmp_path_factory)
