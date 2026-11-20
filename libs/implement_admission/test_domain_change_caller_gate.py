"""AC2: importer inventory, admit verdict and route-notice rows.

Timed walks at lane head (§ 3.10): holder_strings inventory ≈0.34 s / 4358
files (status complete); cdp_ask reach through holder_strings ≈0.01 s.
Both under 2.5 s and 10_000 files at head.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.domain_change_caller_gate import (
    CALLER_INVENTORY_MISSING,
    CALLER_INVENTORY_UNAVAILABLE,
    CALLER_INVENTORY_UNDECLARED,
    ROUTE_PROBE_UNDECLARED,
    build_importer_inventory,
    evaluate_domain_change_caller_admit,
    normalize_expected_path,
)


def _tree_t1(tmp_path: Path) -> Path:
    for rel, body in (
        ("libs/pkg_a/helper.py", "def f():\n    return 1\n"),
        ("libs/pkg_b/user.py", "def g():\n    from pkg_a.helper import f\n    return f()\n"),
        ("services/svc/x.py", "import pkg_a.helper\n"),
        ("libs/pkg_a/sibling.py", "from .helper import f\n"),
        ("libs/pkg_b/test_user.py", "import pkg_a.helper\n"),
        (".wt/copy/libs/pkg_b/user.py", "from pkg_a.helper import f\n"),
        ("libs/pkg_c/notes.py", '# pkg_a.helper is named only here\nX = "pkg_a.helper"\n'),
        ("libs/pkg_d/broken.py", "def (:\n    helper\n"),
        ("libs/pkg_e/node_modules/vendored.py", "import pkg_a.helper\n"),
        ("services/svc/.venv/site.py", "import pkg_a.helper\n"),
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def _tree_t2(tmp_path: Path) -> Path:
    _tree_t1(tmp_path)
    for rel, body in (
        ("libs/cdp_ask/test_active_work.py", "import pkg_a.helper\n"),
        ("libs/cdp_ask/test_other.py", "import pkg_testonly.mod\n"),
        ("libs/pkg_testonly/mod.py", "X = 1\n"),
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def test_inventory_skips_services_relative_import_t1(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    rel = root / "services/svc/rel.py"
    rel.parent.mkdir(parents=True, exist_ok=True)
    rel.write_text("from .pkg_a.helper import f\n", encoding="utf-8")
    inv = build_importer_inventory(
        ["libs/pkg_a/helper.py"],
        repo_root=root,
        files_expected=["libs/pkg_a/helper.py"],
    )
    assert inv.status == "complete"
    assert "services/svc/rel.py" not in [s.path for s in inv.sites]
    assert [(s.path, s.line, s.names) for s in inv.sites] == [
        ("libs/pkg_a/sibling.py", 1, ("f",)),
        ("libs/pkg_b/user.py", 2, ("f",)),
        ("services/svc/x.py", 1, ()),
    ]


def test_inventory_sites_t1(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    inv = build_importer_inventory(
        ["libs/pkg_a/helper.py"],
        repo_root=root,
        files_expected=["libs/pkg_a/helper.py"],
    )
    assert inv.status == "complete"
    assert inv.modules == ("pkg_a.helper",)
    assert [(s.path, s.line, s.names) for s in inv.sites] == [
        ("libs/pkg_a/sibling.py", 1, ("f",)),
        ("libs/pkg_b/user.py", 2, ("f",)),
        ("services/svc/x.py", 1, ()),
    ]
    assert inv.skipped_unparseable == 1


def test_verdict_no_lib_paths_t1(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t1",
        files_expected=["docs/readme.md"],
        attributes={},
        repo_root=root,
    )
    assert verdict.admitted


def test_verdict_undeclared_t1(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t1",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={},
        repo_root=root,
    )
    assert verdict.code == CALLER_INVENTORY_UNDECLARED
    assert "services/svc/x.py:1" in (verdict.reason or "")


def test_verdict_unparseable_declaration_t1(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t1",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={"domain_contract_change": "maybe"},
        repo_root=root,
    )
    assert verdict.code == CALLER_INVENTORY_UNDECLARED


def test_verdict_declared_false_t1(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t1",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={"domain_contract_change": "false"},
        repo_root=root,
    )
    assert verdict.admitted


def test_verdict_declared_true_complete_t1(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t1",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={
            "domain_contract_change": "true",
            "caller_inventory": [
                "libs/pkg_a/sibling.py — re-export",
                "libs/pkg_b/user.py - same call",
                "services/svc/x.py — import only",
            ],
        },
        repo_root=root,
    )
    assert verdict.admitted


def test_verdict_declared_true_missing_t1(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t1",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={
            "domain_contract_change": "true",
            "caller_inventory": [
                "libs/pkg_a/sibling.py — re-export",
                "libs/pkg_b/user.py - same call",
            ],
        },
        repo_root=root,
    )
    assert verdict.code == CALLER_INVENTORY_MISSING
    assert "services/svc/x.py" in (verdict.reason or "")


def test_verdict_rows_t1_incomplete(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t1",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={},
        repo_root=root,
        budget_s=0,
    )
    assert verdict.code == CALLER_INVENTORY_UNAVAILABLE
    assert "time budget reached" in (verdict.reason or "")


def test_verdict_rows_t1_inside_only(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    files = [
        "libs/pkg_a/helper.py",
        "libs/pkg_a/sibling.py",
        "libs/pkg_b/user.py",
        "services/svc/x.py",
    ]
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t1",
        files_expected=files,
        attributes={},
        repo_root=root,
    )
    assert verdict.admitted


def test_share_uri_entry_t1(tmp_path: Path) -> None:
    root = _tree_t1(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t1",
        files_expected=["workspaces://universal-llm-gateway/libs/pkg_a/helper.py"],
        attributes={},
        repo_root=root,
    )
    assert verdict.code == CALLER_INVENTORY_UNDECLARED
    assert normalize_expected_path("/abs/x.py") is None
    assert normalize_expected_path("libs/../x.py") is None
    assert normalize_expected_path("cortex://x") is None


def test_root_not_checkout_t0(tmp_path: Path) -> None:
    path = tmp_path / "libs/pkg_a/helper.py"
    path.parent.mkdir(parents=True)
    path.write_text("def f(): pass\n", encoding="utf-8")
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t0",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={},
        repo_root=tmp_path,
    )
    assert verdict.code == CALLER_INVENTORY_UNAVAILABLE
    assert "the repository root has no libs/ or services/ directory" in (
        verdict.reason or ""
    )


def test_notice_r1_route_probe_undeclared(tmp_path: Path) -> None:
    root = _tree_t2(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t2",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={"domain_contract_change": "false"},
        repo_root=root,
    )
    assert verdict.code == ROUTE_PROBE_UNDECLARED
    assert "pytest libs/cdp_ask/test_active_work.py -q" in (verdict.reason or "")
    assert "GET /v1/project-ask/active-work" in (verdict.reason or "")


def test_notice_r2_route_probe_declared(tmp_path: Path) -> None:
    root = _tree_t2(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t2",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={
            "domain_contract_change": "false",
            "route_probe_tests": ["libs/cdp_ask/test_active_work.py"],
        },
        repo_root=root,
    )
    assert verdict.admitted


def test_notice_r3_test_only_importer_admits(tmp_path: Path) -> None:
    root = _tree_t2(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t2",
        files_expected=["libs/pkg_testonly/mod.py"],
        attributes={},
        repo_root=root,
    )
    assert verdict.admitted


def test_notice_r4_inventory_before_route(tmp_path: Path) -> None:
    root = _tree_t2(tmp_path)
    verdict = evaluate_domain_change_caller_admit(
        todo_id="todo:t2",
        files_expected=["libs/pkg_a/helper.py"],
        attributes={},
        repo_root=root,
    )
    assert verdict.code == CALLER_INVENTORY_UNDECLARED