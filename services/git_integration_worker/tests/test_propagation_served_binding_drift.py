"""Tests for served-vs-working-tree binding drift gate (arc 6627)."""

from __future__ import annotations

import pytest
from openapi_mcp.codegen import ManifestCheckResult

from services.git_integration_worker.cursor_auto.propagation_served_binding_drift import (
    check_served_binding_drift,
)

_LIST_SCOPES_BINDING = {
    "method": "GET",
    "path": "/scopes",
    "operation_id": "get_scopes_scopes_get",
}


def _head_ops_rag_without_list_scopes(_service: str) -> dict[str, dict[str, str]]:
    return {
        "coverage": {
            "method": "GET",
            "path": "/coverage",
            "operation_id": "get_coverage_coverage_get",
        },
        "delete_directory": {
            "method": "DELETE",
            "path": "/directory",
            "operation_id": "delete_directory_directory_delete",
        },
        "delete_source": {
            "method": "DELETE",
            "path": "/source",
            "operation_id": "delete_source_source_delete",
        },
        "orphaned_articles": {
            "method": "GET",
            "path": "/orphaned_articles",
            "operation_id": "get_orphaned_articles_orphaned_articles_get",
        },
        "refresh_hints": {
            "method": "POST",
            "path": "/refresh_corpus_hints",
            "operation_id": "refresh_corpus_hints_refresh_corpus_hints_post",
        },
        "upsert_article": {
            "method": "POST",
            "path": "/article",
            "operation_id": "upsert_article_article_post",
        },
    }


def _rag_served_with_list_scopes() -> dict[str, dict[str, str]]:
    return {
        **_head_ops_rag_without_list_scopes("rag"),
        "list_scopes": _LIST_SCOPES_BINDING,
    }


def test_served_binding_drift_warning_when_served_ahead_of_head():
    """Foreign-WIP courtesy: disk+served parity, HEAD not yet committed."""
    served = _rag_served_with_list_scopes()

    def probe(service: str, *, code_ref: str) -> dict:
        assert service == "rag"
        return {"byte_identical": True, "served_ops": served}

    result = check_served_binding_drift(
        ["rag"],
        probe_fn=probe,
        worktree_ops_fn=lambda _s: served,
        head_ops_fn=_head_ops_rag_without_list_scopes,
    )
    assert result.exit_code == 0
    assert not result.fatal_messages
    assert any(
        "WARNING: unexpected binding for op 'list_scopes'" in msg
        for msg in result.warning_messages
    )


def test_served_binding_drift_warning_when_served_ahead_of_worktree():
    served = _rag_served_with_list_scopes()
    worktree = _head_ops_rag_without_list_scopes("rag")

    def probe(service: str, *, code_ref: str) -> dict:
        assert service == "rag"
        return {"byte_identical": True, "served_ops": served}

    result = check_served_binding_drift(
        ["rag"],
        probe_fn=probe,
        worktree_ops_fn=lambda _s: worktree,
        head_ops_fn=lambda _s: worktree,
    )
    assert result.exit_code == 0
    assert not result.fatal_messages
    assert any(
        "WARNING: unexpected binding for op 'list_scopes'" in msg
        for msg in result.warning_messages
    )


def test_served_binding_drift_fatal_when_served_behind():
    worktree = _head_ops_rag_without_list_scopes("rag")
    served = dict(worktree)
    del served["coverage"]

    def probe(service: str, *, code_ref: str) -> dict:
        return {"byte_identical": True, "served_ops": served}

    result = check_served_binding_drift(
        ["rag"],
        probe_fn=probe,
        worktree_ops_fn=lambda _s: worktree,
        head_ops_fn=lambda _s: worktree,
    )
    assert result.exit_code == 1
    assert any(
        "FATAL: binding lost for op 'coverage'" in msg for msg in result.fatal_messages
    )


def test_served_binding_drift_clean_when_parity():
    ops = _head_ops_rag_without_list_scopes("rag")

    def probe(_service: str, *, code_ref: str) -> dict:
        return {"byte_identical": True, "served_ops": ops}

    result = check_served_binding_drift(
        ["rag"],
        probe_fn=probe,
        worktree_ops_fn=lambda _s: ops,
        head_ops_fn=lambda _s: ops,
    )
    assert result.exit_code == 0
    assert result.fatal_messages == ()
    assert result.warning_messages == ()


@pytest.mark.offline
def test_pre_commit_repo_only_skips_served_binding_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import openapi_mcp_codegen as codegen

    calls: list[str] = []

    def _track(*_args, **_kwargs):
        calls.append("served_binding_drift")
        return ManifestCheckResult((), ())

    monkeypatch.setattr(
        codegen,
        "_check_service_detailed",
        lambda _s, **_k: ManifestCheckResult((), ()),
    )
    monkeypatch.setattr(
        codegen,
        "check_tier_m_manifest_coverage",
        lambda: type(
            "R",
            (),
            {"check_result": ManifestCheckResult((), ())},
        )(),
    )
    from openapi_mcp import commit_snapshot as snap

    from services.git_integration_worker.cursor_auto import (
        propagation_descriptor_drift,
        propagation_served_binding_drift,
    )

    monkeypatch.setattr(
        snap,
        "check_services_from_commit_tree",
        lambda services, **_k: [(s, ManifestCheckResult((), ())) for s in services],
    )
    monkeypatch.setattr(
        propagation_descriptor_drift,
        "check_descriptor_drift",
        lambda **_: propagation_descriptor_drift.DescriptorDriftResult((), ()),
    )
    monkeypatch.setattr(
        propagation_served_binding_drift,
        "check_served_binding_drift",
        _track,
    )
    codegen.main(["--check", "--service", "all", "--repo-only"])
    assert calls == []


@pytest.mark.offline
def test_fleet_check_invokes_served_binding_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import openapi_mcp_codegen as codegen

    calls: list[str] = []

    def _track(*_args, **_kwargs):
        calls.append("served_binding_drift")
        return ManifestCheckResult((), ())

    monkeypatch.setattr(
        codegen,
        "_check_service_detailed",
        lambda _s: ManifestCheckResult((), ()),
    )
    monkeypatch.setattr(
        codegen,
        "check_tier_m_manifest_coverage",
        lambda: type(
            "R",
            (),
            {"check_result": ManifestCheckResult((), ())},
        )(),
    )
    from services.git_integration_worker.cursor_auto import (
        propagation_descriptor_drift,
        propagation_served_binding_drift,
    )

    monkeypatch.setattr(
        propagation_descriptor_drift,
        "check_descriptor_drift",
        lambda **_: propagation_descriptor_drift.DescriptorDriftResult((), ()),
    )
    monkeypatch.setattr(
        propagation_served_binding_drift,
        "check_served_binding_drift",
        _track,
    )
    codegen.main(["--check", "--service", "all"])
    assert calls == ["served_binding_drift"]
