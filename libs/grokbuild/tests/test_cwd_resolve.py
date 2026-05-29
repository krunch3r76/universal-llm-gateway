"""Unit tests for grokbuild.cwd_resolve."""

from __future__ import annotations

from grokbuild.cwd_resolve import resolve_cwd


def test_resolve_bare_repo_name() -> None:
    path, reason = resolve_cwd(None, "universal-llm-gateway")
    assert reason == ""
    assert path == "/mnt/torus/projects/universal-llm-gateway"


def test_resolve_container_path() -> None:
    path, reason = resolve_cwd("/data/project/universal-llm-gateway/x", None)
    assert reason == ""
    assert path == "/mnt/torus/projects/universal-llm-gateway/x"


def test_resolve_host_path_unchanged() -> None:
    host = "/mnt/torus/projects/foo"
    path, reason = resolve_cwd(host, None)
    assert reason == ""
    assert path == host


def test_resolve_both_missing() -> None:
    path, reason = resolve_cwd(None, None)
    assert path == ""
    assert reason == "one of cwd or source_repo is required"
