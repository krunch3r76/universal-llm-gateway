"""Consumer verification gate tests."""

from __future__ import annotations

from cursorbuild.green_gate import CONSUMER_FIXTURE_NODEIDS, run_consumer_verification


def test_consumer_fixture_paths_exist() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    for rel in CONSUMER_FIXTURE_NODEIDS:
        path = root / rel.split("::")[0]
        assert path.is_file(), rel


def test_consumer_gate_skips_empty_diff() -> None:
    result = run_consumer_verification(changed_py_files=[])
    assert result.ok
    assert "skipped" in result.detail
