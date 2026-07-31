"""Unit tests for life-surface fs sandbox default + hint contracts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tool_error_enricher import (
    apply_life_sandbox_default,
    fs_missing_sandbox_hint,
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
    assert "defaults to cortex" in err
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
