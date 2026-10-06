"""Unit tests for life-surface fs sandbox default + hint contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tool_error_enricher import (
    apply_life_sandbox_default,
    fs_missing_sandbox_hint,
    life_cortex_repo_stub_alias,
    life_workspaces_fs_refusal,
)


def test_life_blank_relative_path_defaults_to_cortex():
    assert (
        apply_life_sandbox_default(
            surface="life",
            sandbox="",
            path="notes/system/threads/probe.md",
        )
        == "cortex"
    )


def test_life_empty_path_defaults_to_cortex():
    assert apply_life_sandbox_default(surface="life", sandbox="", path="") == "cortex"


def test_life_share_uri_does_not_force_cortex():
    assert (
        apply_life_sandbox_default(
            surface="life",
            sandbox="",
            path="cortex://notes/system/threads/probe.md",
        )
        == ""
    )
    assert (
        apply_life_sandbox_default(
            surface="life",
            sandbox="",
            path="workspaces://universal-llm-gateway/README.md",
        )
        == ""
    )


def test_life_absolute_path_left_alone_for_mount_ingress():
    assert (
        apply_life_sandbox_default(
            surface="life",
            sandbox="",
            path="/mnt/torus/mcp-data/notes/x.md",
        )
        == ""
    )


def test_code_blank_does_not_default():
    assert (
        apply_life_sandbox_default(
            surface="code",
            sandbox="",
            path="notes/system/threads/probe.md",
        )
        == ""
    )


def test_life_explicit_sandbox_preserved():
    assert (
        apply_life_sandbox_default(
            surface="life",
            sandbox="cortex",
            path="notes/x.md",
        )
        == "cortex"
    )


def test_life_missing_sandbox_hint_never_says_both_stores():
    hint = fs_missing_sandbox_hint("notes/system/threads/x.md", surface="life")
    assert "both stores" not in hint.lower()
    assert "cortex" in hint.lower()


def test_code_missing_sandbox_hint_keeps_ambiguous_advisory():
    hint = fs_missing_sandbox_hint("notes/system/threads/x.md", surface="code")
    assert "BOTH stores" in hint or "both stores" in hint.lower()


def test_life_workspaces_refusal_mentions_default():
    err = life_workspaces_fs_refusal()["error"]
    assert "READ-ONLY" in err
    assert "/mcp/code" in err


def test_life_default_then_ingress_resolves_bare_notes(tmp_path, monkeypatch):
    """Integration: life default + resolve_fs_ingress accepts Fable-shaped write."""
    from implement_admission.scheme_resolve import resolve_fs_ingress

    cortex_root = tmp_path / "cortex"
    (cortex_root / "notes" / "system" / "threads").mkdir(parents=True)
    sandbox = apply_life_sandbox_default(
        surface="life",
        sandbox="",
        path="notes/system/threads/4917-entity-implies-map-fable-design.md",
    )
    assert sandbox == "cortex"
    ingress = resolve_fs_ingress(
        "notes/system/threads/4917-entity-implies-map-fable-design.md",
        sandbox=sandbox,
        cortex_root=cortex_root,
        workspaces_root_override=tmp_path / "projects",
    )
    assert ingress.sandbox == "cortex"
    assert ingress.rel_path.endswith("4917-entity-implies-map-fable-design.md")


def test_life_empty_cortex_repo_stub_aliases_read_to_workspaces(tmp_path, monkeypatch):
    """a:38194 — empty cortex repo stub must not trap life reads."""
    from tools._project_paths import repo_roots

    projects = tmp_path / "projects"
    repo = projects / "universal-llm-gateway"
    (repo / ".git").mkdir(parents=True)
    (repo / "README.md").write_text("ok\n", encoding="utf-8")
    cortex = tmp_path / "cortex"
    stub = cortex / "universal-llm-gateway" / "docs" / "research"
    stub.mkdir(parents=True)

    monkeypatch.setenv("PROJECT_ROOT", str(projects))
    import tools._project_paths as paths_mod

    paths_mod._PROJECT_ROOT = projects
    assert "universal-llm-gateway" in {r.name for r in repo_roots(projects)}

    sandbox = apply_life_sandbox_default(
        surface="life",
        sandbox="",
        path="universal-llm-gateway/README.md",
    )
    assert sandbox == "cortex"
    aliased, err = life_cortex_repo_stub_alias(
        surface="life",
        sandbox=sandbox,
        path="universal-llm-gateway/README.md",
        for_write=False,
        cortex_root=cortex,
    )
    assert err is None
    assert aliased == "workspaces"


def test_life_populated_cortex_repo_stays_cortex(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    repo = projects / "universal-llm-gateway"
    (repo / ".git").mkdir(parents=True)
    cortex = tmp_path / "cortex"
    target = cortex / "universal-llm-gateway" / "notes"
    target.mkdir(parents=True)
    (target / "kept.md").write_text("cortex content\n", encoding="utf-8")

    monkeypatch.setenv("PROJECT_ROOT", str(projects))
    import tools._project_paths as paths_mod

    paths_mod._PROJECT_ROOT = projects

    aliased, err = life_cortex_repo_stub_alias(
        surface="life",
        sandbox="cortex",
        path="universal-llm-gateway/notes/kept.md",
        for_write=False,
        cortex_root=cortex,
    )
    assert err is None
    assert aliased is None


def test_life_empty_cortex_repo_stub_write_refuses(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    repo = projects / "universal-llm-gateway"
    (repo / ".git").mkdir(parents=True)
    cortex = tmp_path / "cortex"
    (cortex / "universal-llm-gateway" / "docs").mkdir(parents=True)

    monkeypatch.setenv("PROJECT_ROOT", str(projects))
    import tools._project_paths as paths_mod

    paths_mod._PROJECT_ROOT = projects

    aliased, err = life_cortex_repo_stub_alias(
        surface="life",
        sandbox="cortex",
        path="universal-llm-gateway/README.md",
        for_write=True,
        cortex_root=cortex,
    )
    assert aliased is None
    assert err is not None
    assert "empty cortex stub" in err
    assert "workspaces://universal-llm-gateway" in err


def test_life_nested_cortex_files_are_not_a_stub(tmp_path, monkeypatch):
    """N3 — files two levels deep (agent-bus/attachments) keep cortex."""
    from tools._project_paths import repo_roots

    projects = tmp_path / "projects"
    repo = projects / "agent-bus"
    (repo / ".git").mkdir(parents=True)
    cortex = tmp_path / "cortex"
    nested = cortex / "agent-bus" / "attachments"
    nested.mkdir(parents=True)
    (nested / "a.md").write_text("kept\n", encoding="utf-8")

    monkeypatch.setenv("PROJECT_ROOT", str(projects))
    import tools._project_paths as paths_mod

    paths_mod._PROJECT_ROOT = projects
    assert "agent-bus" in {r.name for r in repo_roots(projects)}

    aliased, err = life_cortex_repo_stub_alias(
        surface="life",
        sandbox="cortex",
        path="agent-bus/attachments/a.md",
        for_write=False,
        cortex_root=cortex,
    )
    assert err is None
    assert aliased is None
    write_aliased, write_err = life_cortex_repo_stub_alias(
        surface="life",
        sandbox="cortex",
        path="agent-bus/attachments/b.md",
        for_write=True,
        cortex_root=cortex,
    )
    assert write_err is None
    assert write_aliased is None


def test_life_copy_to_empty_cortex_stub_refuses(tmp_path, monkeypatch):
    """B2 — copy dest into empty repo stub must not plant a file there."""
    from tools.filesystem._batch_ingress import resolve_copy_target_ingress

    projects = tmp_path / "projects"
    repo = projects / "universal-llm-gateway"
    (repo / ".git").mkdir(parents=True)
    cortex = tmp_path / "cortex"
    stub = cortex / "universal-llm-gateway" / "docs"
    stub.mkdir(parents=True)
    (cortex / "notes").mkdir()
    (cortex / "notes" / "x.md").write_text("src\n", encoding="utf-8")

    monkeypatch.setenv("PROJECT_ROOT", str(projects))
    import tools._project_paths as paths_mod

    paths_mod._PROJECT_ROOT = projects

    with pytest.raises(ValueError, match="empty cortex stub"):
        resolve_copy_target_ingress(
            "universal-llm-gateway/x.md",
            target_sandbox="",
            source_sandbox="cortex",
            cortex_root=cortex,
            surface="life",
        )
    assert not (cortex / "universal-llm-gateway" / "x.md").exists()
    assert not any(p.is_file() for p in (cortex / "universal-llm-gateway").rglob("*"))
