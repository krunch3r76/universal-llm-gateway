"""AC6: route-probe reach table and map-integrity guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.consumer_import_verify import repo_root
from implement_admission.route_probe_reach import (
    ROUTE_PROBE_EVIDENCE,
    route_probe_lib_reach,
)


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "libs/cdp_ask").mkdir(parents=True)
    (tmp_path / "libs/pkg_route").mkdir(parents=True)
    (tmp_path / "libs/pkg_testonly").mkdir(parents=True)
    (tmp_path / "libs/cdp_ask/test_active_work.py").write_text(
        "from cdp_ask.app import create_app\n",
        encoding="utf-8",
    )
    (tmp_path / "libs/cdp_ask/app.py").write_text(
        "def create_app():\n"
        "    import pkg_route.helper\n"
        "    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "libs/pkg_route/helper.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "libs/cdp_ask/test_other.py").write_text(
        "import pkg_testonly.mod\n",
        encoding="utf-8",
    )
    (tmp_path / "libs/pkg_testonly/mod.py").write_text("X = 1\n", encoding="utf-8")
    return tmp_path


def test_reach_seeded_from_mapped_module(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    assert route_probe_lib_reach("cdp_ask", "libs/pkg_route/helper.py", root=root)
    assert not route_probe_lib_reach("cdp_ask", "libs/pkg_testonly/mod.py", root=root)


def test_reach_not_cached_across_calls(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    assert not route_probe_lib_reach("cdp_ask", "libs/pkg_testonly/mod.py", root=root)
    app = root / "libs/cdp_ask/app.py"
    app.write_text(app.read_text(encoding="utf-8") + "import pkg_testonly.mod\n")
    assert route_probe_lib_reach("cdp_ask", "libs/pkg_testonly/mod.py", root=root)


def test_reach_overrun_counts_as_reached(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    assert route_probe_lib_reach(
        "cdp_ask", "libs/pkg_testonly/mod.py", root=root, max_files=1
    )


def test_reach_absent_seed_is_false(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    (root / "libs").mkdir()
    assert not route_probe_lib_reach(
        "cdp_ask", "libs/pkg_route/helper.py", root=root
    )


def test_reach_unmapped_slug_and_test_module_are_false(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    assert not route_probe_lib_reach("rag", "libs/pkg_route/helper.py", root=root)
    assert not route_probe_lib_reach(
        "cdp_ask", "libs/cdp_ask/test_other.py", root=root
    )


def test_route_probe_map_matches_checkout() -> None:
    root = repo_root()
    for slug, ev in ROUTE_PROBE_EVIDENCE.items():
        assert (root / ev.test_module).is_file()
        assert ev.path in (root / ev.test_module).read_text(encoding="utf-8")
    assert (
        '@app.get("/v1/project-ask/active-work")'
        in (root / "libs/cdp_ask/app.py").read_text(encoding="utf-8")
    )
    assert (
        ROUTE_PROBE_EVIDENCE["cdp_ask"].canonical_command
        == "pytest libs/cdp_ask/test_active_work.py -q"
    )