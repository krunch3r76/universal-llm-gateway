"""Inverse unmapped-importer authorship gate (predicate, not a census list)."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.unmapped_importer_verify import (
    check_unmapped_importers,
    imported_unmapped_census,
    is_service_test_module_path,
)


def test_is_service_test_module_path_skips_tests_keeps_runtime() -> None:
    assert is_service_test_module_path(
        "services/git_integration_worker/tests/test_hop_seat_cutover_refuse.py"
    )
    assert is_service_test_module_path("services/mcp-server/conftest.py")
    assert not is_service_test_module_path(
        "services/mcp-server/tools/agent_bus/request.py"
    )


def _write_unmapped_fixture(root: Path) -> None:
    lib = root / "libs" / "ghost_lib"
    lib.mkdir(parents=True)
    (lib / "__init__.py").write_text(
        '"""Fixture lib with no nomination union."""\n', encoding="utf-8"
    )
    importer = root / "services" / "mcp-server" / "tools"
    importer.mkdir(parents=True)
    (importer / "ghost_import.py").write_text(
        "from ghost_lib import missing_consumers\n",
        encoding="utf-8",
    )


@pytest.mark.offline
def test_check_unmapped_importers_fails_on_undeclared_fixture(tmp_path: Path) -> None:
    """Negative control: the gate must fail when a service imports an unmapped lib.

    A check that cannot go red is not a gate. This fixture is the standing
    proof; the live-tree census is a separate cleanliness assertion.
    """
    _write_unmapped_fixture(tmp_path)
    failures = check_unmapped_importers(root=tmp_path)
    assert failures, "predicate produced no failure on an undeclared services/ import"
    joined = "\n".join(failures)
    assert "ghost_lib" in joined
    assert "mcp" in joined
    assert "nomination union empty" in joined
    assert "services/mcp-server/tools/ghost_import.py" in joined


@pytest.mark.offline
def test_check_unmapped_importers_ignores_type_checking_only(tmp_path: Path) -> None:
    lib = tmp_path / "libs" / "ghost_lib"
    lib.mkdir(parents=True)
    (lib / "__init__.py").write_text(
        '"""Fixture lib with no nomination union."""\n', encoding="utf-8"
    )
    importer = tmp_path / "services" / "mcp-server" / "tools"
    importer.mkdir(parents=True)
    (importer / "ghost_import.py").write_text(
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from ghost_lib import missing_consumers\n",
        encoding="utf-8",
    )
    assert check_unmapped_importers(root=tmp_path) == []


@pytest.mark.offline
def test_check_unmapped_importers_passes_when_consumers_declared(
    tmp_path: Path,
) -> None:
    _write_unmapped_fixture(tmp_path)
    init = tmp_path / "libs" / "ghost_lib" / "__init__.py"
    init.write_text(
        '"""Fixture lib now declared."""\nCONSUMERS: tuple[str, ...] = ("mcp",)\n',
        encoding="utf-8",
    )
    assert check_unmapped_importers(root=tmp_path) == []


@pytest.mark.offline
def test_imported_unmapped_census_tree_is_empty() -> None:
    """Live-tree cleanliness: imported-unmapped package×slug count must be 0."""
    census = imported_unmapped_census()
    assert census["pair_count"] == 0, (
        f"imported-unmapped still {census['pair_count']}: {census['pairs']}"
    )
    assert check_unmapped_importers() == []
