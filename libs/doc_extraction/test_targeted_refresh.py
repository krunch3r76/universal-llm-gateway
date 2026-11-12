"""Smoke tests for libs/doc_extraction/targeted_refresh.py."""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from doc_extraction.targeted_refresh import (
    SCOPE_SOURCE_COMMIT_PATTERN,
    TargetedRefreshError,
    main,
    refresh_gateway,
    refresh_routing,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REFRESHED_BY = "agent-bus:23059"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def hermetic_workspace():
    """Scratch tree under repo tmp/ — pytest tmp_path often contains 'test' in the path."""
    root = _REPO_ROOT / "tmp" / f"targeted-refresh-{uuid.uuid4().hex[:10]}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _replace_scope_source_commit(text: str, head: str) -> str:
    return re.sub(
        SCOPE_SOURCE_COMMIT_PATTERN,
        f"source commit `{head}`",
        text,
    )


def _scope_section(text: str) -> str:
    start = text.index("## Scope of this pass")
    chunk = text[start:]
    end = chunk.index("\n---")
    return chunk[:end]


def test_main_usage_returns_2() -> None:
    assert main([]) == 2
    assert main(["routing"]) == 2
    assert main(["gateway", str(_REPO_ROOT), "extra"]) == 2
    assert main(["gateway", str(_REPO_ROOT)]) == 2
    assert (
        main(
            ["gateway", str(_REPO_ROOT), "--refreshed-by", _REFRESHED_BY, "extra"]
        )
        == 2
    )


def test_refresh_routing_stamp_and_scope_trim() -> None:
    text = refresh_routing(_REPO_ROOT, refreshed_by=_REFRESHED_BY)
    pre_key = text.split("## Key Functions", 1)[0]
    assert "method=targeted-refresh" in pre_key
    scope = _scope_section(pre_key)
    assert "Step 9 subset" not in scope
    assert "Key Functions" in scope
    assert "full production routing tree" in scope
    assert f"refreshed_by={_REFRESHED_BY}" in pre_key


def test_scope_source_commit_substitution_replaces_arbitrary_prior() -> None:
    prior = "0d45bbef52c8"
    new_head = "abc123def456"
    scope_line = (
        f"**Targeted refresh** against routing at source commit `{prior}`. "
        "Inventory unchanged."
    )
    out = _replace_scope_source_commit(scope_line, new_head)
    assert f"source commit `{new_head}`" in out
    assert prior not in out


def test_scope_source_commit_substitution_idempotent() -> None:
    text = "at source commit `c75e3fc8`. prose."
    head = "254be89cb7ee"
    once = _replace_scope_source_commit(text, head)
    twice = _replace_scope_source_commit(once, head)
    assert once == twice
    assert f"source commit `{head}`" in twice


def test_scope_source_commit_substitution_preserves_surrounding_text() -> None:
    prior = "0d45bbef52c8"
    new_head = "deadbeefcafe"
    scope_line = (
        f"**Targeted refresh** (Kaywan) at source commit `{prior}`. "
        f"**full module inventory (57/57)**"
    )
    out = _replace_scope_source_commit(scope_line, new_head)
    assert out == scope_line.replace(
        f"source commit `{prior}`",
        f"source commit `{new_head}`",
    )


def test_refresh_routing_scope_source_commit_matches_git_head() -> None:
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        text=True,
    ).strip()[:12]
    text = refresh_routing(_REPO_ROOT, refreshed_by=_REFRESHED_BY)
    scope = _scope_section(text.split("## Key Functions", 1)[0])
    assert f"source commit `{head}`" in scope


def test_counts_derived_from_inventory_and_refreshed_by_required(
    hermetic_workspace: Path,
) -> None:
    _init_git_repo(hermetic_workspace)
    routing_root = hermetic_workspace / "services/universal-stargate/systems/routing"
    routing_root.mkdir(parents=True)
    (routing_root / "alpha.py").write_text(
        '"""Alpha module for inventory count."""\n',
        encoding="utf-8",
    )
    docs = hermetic_workspace / "docs/architecture"
    docs.mkdir(parents=True)
    routing_doc = docs / "routing.md"
    routing_doc.write_text(
        "\n".join(
            [
                "# Routing test",
                "",
                "<!-- GENERATED:START generated=2020-01-01 method=targeted-refresh "
                "coverage=1/1 -->",
                "",
                "## Scope of this pass",
                "",
                "**Targeted refresh** at source commit `000000000000`.",
                "",
                "---",
                "",
                "## Module Inventory (1/1)",
                "",
                "| Module | Description |",
                "|---|---|",
                "| `alpha.py` | Alpha module for inventory count. |",
                "",
                "---",
                "",
                "## Key Functions",
                "",
                "stub",
                "",
                "## Imports and Dependencies (full routing tree)",
                "",
                "stub",
                "",
                "<!-- AUTHORED -->",
                "## Synthesis",
                "",
                "Module Inventory (1/1).",
                "<!-- GENERATED:END -->",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["routing", str(hermetic_workspace)]) == 2

    text = refresh_routing(hermetic_workspace, refreshed_by="lane:hermetic-99")
    assert "refreshed_by=lane:hermetic-99" in text.split("## Key Functions", 1)[0]
    assert "agent-bus:23032" not in text
    assert re.search(r"coverage=\d+/\d+", text)
    assert "## Module Inventory (1/1)" in text


def test_stamp_coverage_matches_rendered_inventory_rows_not_mod_n_over_mod_n(
    hermetic_workspace: Path,
) -> None:
    _init_git_repo(hermetic_workspace)
    routing_root = hermetic_workspace / "services/universal-stargate/systems/routing"
    routing_root.mkdir(parents=True)
    (routing_root / "one.py").write_text('"""One."""\n', encoding="utf-8")
    (routing_root / "two.py").write_text('"""Two."""\n', encoding="utf-8")

    docs = hermetic_workspace / "docs/architecture"
    docs.mkdir(parents=True)
    stale_inventory = "\n".join(
        [
            "| Module | Description |",
            "|---|---|",
            "| `one.py` | One. |",
        ]
    )
    routing_doc = docs / "routing.md"
    routing_doc.write_text(
        "\n".join(
            [
                "# Routing",
                "<!-- GENERATED:START generated=2020-01-01 "
                "method=targeted-refresh coverage=1/1 -->",
                "## Scope",
                "---",
                "## Module Inventory (1/1)",
                "",
                stale_inventory,
                "",
                "---",
                "## Key Functions",
                "x",
                "## Imports and Dependencies (full routing tree)",
                "y",
                "<!-- AUTHORED -->",
                "## Synthesis",
                "<!-- GENERATED:END -->",
            ]
        ),
        encoding="utf-8",
    )

    text = refresh_routing(hermetic_workspace, refreshed_by=_REFRESHED_BY)
    pre = text.split("## Key Functions", 1)[0]
    m_stamp = re.search(r"coverage=(\d+)/(\d+)", pre)
    assert m_stamp is not None
    covered, total = int(m_stamp.group(1)), int(m_stamp.group(2))
    assert total == 2
    assert covered == 2
    assert "two.py" in pre
    assert "coverage=2/2" in pre or f"coverage={covered}/{total}" in pre
    assert text.count("## Key Functions (full routing tree)") == 1


def test_missing_gateway_or_routing_marker_raises(tmp_path: Path) -> None:
    gw_docs = tmp_path / "docs/architecture"
    gw_docs.mkdir(parents=True)
    (gw_docs / "gateway.md").write_text("# no section markers\n", encoding="utf-8")
    gw_dir = tmp_path / "services/_universal-llm-gateway"
    gw_dir.mkdir(parents=True)
    (gw_dir / "mod.py").write_text('"""m"""\n', encoding="utf-8")

    with pytest.raises(TargetedRefreshError, match="## 1\\."):
        refresh_gateway(tmp_path, refreshed_by=_REFRESHED_BY)

    _init_git_repo(tmp_path)
    rt_docs = tmp_path / "docs/architecture"
    (rt_docs / "routing.md").write_text("# no key functions\n", encoding="utf-8")
    rt = tmp_path / "services/universal-stargate/systems/routing"
    rt.mkdir(parents=True)
    (rt / "a.py").write_text('"""a"""\n', encoding="utf-8")

    with pytest.raises(TargetedRefreshError, match="Key Functions"):
        refresh_routing(tmp_path, refreshed_by=_REFRESHED_BY)


def _normalize_refresh_text(text: str) -> str:
    text = re.sub(r"generated=\d{4}-\d{2}-\d{2}", "generated=DATE", text)
    text = re.sub(
        r"<!-- GENERATED:START generated=DATE[^>]*-->",
        "<!-- GENERATED:START -->",
        text,
    )
    return text


def _gateway_fixture_doc(prior_commit: str | None) -> str:
    stamp = (
        "<!-- GENERATED:START generated=2020-01-01 method=targeted-refresh "
        f"inventory_sha=abc source_commit={prior_commit or '000000000000'} "
        "refreshed_by=fixture packages=1/1 modules=1/1 -->"
        if prior_commit
        else "<!-- GENERATED:START generated=2020-01-01 method=targeted-refresh "
        "inventory_sha=abc refreshed_by=fixture packages=1/1 modules=1/1 -->"
    )
    return "\n".join(
        [
            "# Gateway fixture",
            "",
            stamp,
            "",
            "> disclaimer",
            "",
            "## 1. Section one",
            "",
            "Authored prose for sections 1-10.",
            "",
            "## 11. Per-package module inventory",
            "",
            "Generated §11 placeholder.",
            "",
            "<!-- GENERATED:END -->",
            "",
            "---",
            "",
            "### missing_coverage",
            "",
            "Stale missing_coverage body.",
            "",
            "### unsupported_claims",
            "",
            "tail",
        ]
    )


def _normalize_gateway_output(text: str) -> str:
    text = re.sub(r"generated=\d{4}-\d{2}-\d{2}", "generated=DATE", text)
    text = re.sub(r"source_commit=[0-9a-f]{7,40}", "source_commit=COMMIT", text)
    text = re.sub(
        r"inventory_sha=[0-9a-f]{12}",
        "inventory_sha=SHA",
        text,
    )
    return text


def _carried_from_value(text: str) -> str:
    m = re.search(
        r"<!-- AUTHORED carried_from=([^\s>]+) -->",
        text,
    )
    assert m is not None
    return m.group(1)


def test_refresh_gateway_twice_idempotent_carried_from_and_markers(
    hermetic_workspace: Path,
) -> None:
    _init_git_repo(hermetic_workspace)
    prior = "0d45bbef52c8"
    gw_dir = hermetic_workspace / "services/_universal-llm-gateway"
    gw_dir.mkdir(parents=True)
    (gw_dir / "mod.py").write_text('"""Gateway module."""\n', encoding="utf-8")
    docs = hermetic_workspace / "docs/architecture"
    docs.mkdir(parents=True)
    doc_path = docs / "gateway.md"
    doc_path.write_text(_gateway_fixture_doc(prior), encoding="utf-8")

    run1 = refresh_gateway(hermetic_workspace, refreshed_by=_REFRESHED_BY)
    doc_path.write_text(run1, encoding="utf-8")
    subprocess.run(
        ["git", "add", "docs/architecture/gateway.md"],
        cwd=hermetic_workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "refresh gateway once"],
        cwd=hermetic_workspace,
        check=True,
        capture_output=True,
    )

    run2 = refresh_gateway(hermetic_workspace, refreshed_by=_REFRESHED_BY)
    doc_path.write_text(run2, encoding="utf-8")
    subprocess.run(
        ["git", "add", "docs/architecture/gateway.md"],
        cwd=hermetic_workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "refresh gateway twice"],
        cwd=hermetic_workspace,
        check=True,
        capture_output=True,
    )

    run3 = refresh_gateway(hermetic_workspace, refreshed_by=_REFRESHED_BY)

    assert _normalize_gateway_output(run1) == _normalize_gateway_output(run2)
    assert _normalize_gateway_output(run2) == _normalize_gateway_output(run3)
    assert _carried_from_value(run1) == prior
    assert _carried_from_value(run2) == prior
    assert _carried_from_value(run3) == prior
    assert run3.count("<!-- AUTHORED carried_from=") == 1
    assert run3.count("<!-- /AUTHORED carried_from=") == 1


def test_refresh_gateway_no_prior_stamp_carried_from_unknown(
    hermetic_workspace: Path,
) -> None:
    _init_git_repo(hermetic_workspace)
    gw_dir = hermetic_workspace / "services/_universal-llm-gateway"
    gw_dir.mkdir(parents=True)
    (gw_dir / "mod.py").write_text('"""Gateway module."""\n', encoding="utf-8")
    docs = hermetic_workspace / "docs/architecture"
    docs.mkdir(parents=True)
    doc_path = docs / "gateway.md"
    doc_path.write_text(_gateway_fixture_doc(prior_commit=None), encoding="utf-8")
    text = refresh_gateway(hermetic_workspace, refreshed_by=_REFRESHED_BY)
    assert _carried_from_value(text) == "unknown"


def test_refresh_gateway_unmatched_authored_open_raises(
    hermetic_workspace: Path,
) -> None:
    _init_git_repo(hermetic_workspace)
    gw_dir = hermetic_workspace / "services/_universal-llm-gateway"
    gw_dir.mkdir(parents=True)
    (gw_dir / "mod.py").write_text('"""m"""\n', encoding="utf-8")
    body = _gateway_fixture_doc("aaaaaaaabbbb")
    body = body.replace(
        "## 1. Section one",
        "<!-- AUTHORED carried_from=aaaaaaaabbbb -->\n## 1. Section one",
    )
    docs = hermetic_workspace / "docs/architecture"
    docs.mkdir(parents=True)
    (docs / "gateway.md").write_text(body, encoding="utf-8")
    with pytest.raises(TargetedRefreshError, match="carried_from"):
        refresh_gateway(hermetic_workspace, refreshed_by=_REFRESHED_BY)


@pytest.mark.parametrize(
    "body_mutator,match",
    [
        (lambda b: b.replace("<!-- GENERATED:END -->", ""), "GENERATED:END"),
        (
            lambda b: b.replace(
                "<!-- GENERATED:END -->",
                "<!-- GENERATED:END -->\n<!-- GENERATED:END -->",
            ),
            "multiple",
        ),
    ],
)
def test_refresh_gateway_generated_end_marker_raises(
    hermetic_workspace: Path,
    body_mutator,
    match: str,
) -> None:
    _init_git_repo(hermetic_workspace)
    gw_dir = hermetic_workspace / "services/_universal-llm-gateway"
    gw_dir.mkdir(parents=True)
    (gw_dir / "mod.py").write_text('"""m"""\n', encoding="utf-8")
    docs = hermetic_workspace / "docs/architecture"
    docs.mkdir(parents=True)
    (docs / "gateway.md").write_text(
        body_mutator(_gateway_fixture_doc("cccccccccccc")),
        encoding="utf-8",
    )
    with pytest.raises(TargetedRefreshError, match=match):
        refresh_gateway(hermetic_workspace, refreshed_by=_REFRESHED_BY)


def test_refresh_twice_is_idempotent_ignoring_date(hermetic_workspace: Path) -> None:
    _init_git_repo(hermetic_workspace)
    routing_root = hermetic_workspace / "services/universal-stargate/systems/routing"
    routing_root.mkdir(parents=True)
    (routing_root / "mod.py").write_text('"""Mod doc."""\n', encoding="utf-8")

    docs = hermetic_workspace / "docs/architecture"
    docs.mkdir(parents=True)
    (docs / "routing.md").write_text(
        "\n".join(
            [
                "# R",
                "<!-- GENERATED:START generated=2020-01-01 "
                "method=targeted-refresh coverage=1/1 -->",
                "## Scope",
                "---",
                "## Module Inventory (1/1)",
                "",
                "| Module | Description |",
                "| `mod.py` | Mod doc. |",
                "",
                "---",
                "## Key Functions",
                "old",
                "## Imports and Dependencies (full routing tree)",
                "old",
                "<!-- AUTHORED -->",
                "## Synthesis",
                "<!-- GENERATED:END -->",
            ]
        ),
        encoding="utf-8",
    )

    once = refresh_routing(hermetic_workspace, refreshed_by=_REFRESHED_BY)
    (docs / "routing.md").write_text(once, encoding="utf-8")
    twice = refresh_routing(hermetic_workspace, refreshed_by=_REFRESHED_BY)
    assert _normalize_refresh_text(once) == _normalize_refresh_text(twice)
