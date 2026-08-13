"""Unit tests for mint-time close-surface composition."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from implement_admission.propagation_close_surfaces import (
    compose_close_surfaces,
    compose_proof_for_surfaces,
    excluded_surfaces_to_payload,
    resolve_close_surfaces,
)
from implement_admission.propagation_row import rows_from_lib_consumers


def test_single_consumer_commit_excludes_cortex_api() -> None:
    """One verified consumer (mcp) — cortex_api excluded with evidence."""
    paths = ["libs/claude_bundles/request_admission_identity.py"]

    def fake_verify(slug: str, _path: str) -> str:
        return "verified" if slug == "mcp" else "contradicted"

    with patch(
        "implement_admission.propagation_close_surfaces.verify_consumer_import",
        side_effect=fake_verify,
    ):
        composition = compose_close_surfaces("mcp", "client_visible", paths)

    assert composition.close_surfaces == ("mcp_health",)
    assert len(composition.excluded_surfaces) == 1
    assert composition.excluded_surfaces[0].surface == "cortex_api"
    assert composition.excluded_surfaces[0].import_path == "contradicted"
    assert paths[0] in composition.excluded_surfaces[0].evidence_paths

    proof = compose_proof_for_surfaces("mcp", "client_visible", composition.close_surfaces)
    assert "cortex-api" not in proof.lower()
    assert "GET /health" in proof


def test_multi_consumer_commit_includes_both_surfaces() -> None:
    """Both mcp and cortex_api verified — full client_visible composite."""
    paths = ["libs/deploy_identity/__init__.py"]

    def fake_verify(slug: str, _path: str) -> str:
        return "verified"

    with patch(
        "implement_admission.propagation_close_surfaces.verify_consumer_import",
        side_effect=fake_verify,
    ):
        composition = compose_close_surfaces("mcp", "client_visible", paths)

    assert set(composition.close_surfaces) == {"mcp_health", "cortex_api"}
    assert composition.excluded_surfaces == ()


def test_commit_paths_reach_no_optional_consumer() -> None:
    """Non-lib paths with no cortex reach — only mcp_health owed."""
    composition = compose_close_surfaces(
        "mcp",
        "client_visible",
        ["services/mcp-server/tools/agent_bus/request.py"],
    )
    assert composition.close_surfaces == ("mcp_health",)
    assert composition.excluded_surfaces[0].import_path == "not_probed"


def test_rows_from_lib_consumers_single_consumer_mints_exclusion_record() -> None:
    """Mint path persists exclusion on the row for downstream settle."""
    paths = ["libs/deploy_identity/__init__.py"]

    def fake_verify(slug: str, _path: str) -> str:
        return "verified" if slug == "mcp" else "contradicted"

    with patch(
        "implement_admission.propagation_close_surfaces.verify_consumer_import",
        side_effect=fake_verify,
    ), patch(
        "implement_admission.propagation_row.verify_consumer_import",
        side_effect=fake_verify,
    ):
        rows, escalations = rows_from_lib_consumers(paths, code_ref="abc123")

    assert len(rows) == 1
    assert rows[0].service == "mcp"
    assert rows[0].close_surfaces == ("mcp_health",)
    assert rows[0].excluded_surfaces is not None
    assert rows[0].excluded_surfaces[0]["surface"] == "cortex_api"
    assert len(escalations) == 1
    assert "git_integration_worker" in escalations[0]


def test_rows_from_lib_consumers_nothing_verified_escalates() -> None:
    """No verified consumers — no row minted; lead-visible escalation."""
    paths = ["libs/deploy_identity/__init__.py"]

    def fake_verify(_slug: str, _path: str) -> str:
        return "contradicted"

    with patch(
        "implement_admission.propagation_row.verify_consumer_import",
        side_effect=fake_verify,
    ):
        rows, escalations = rows_from_lib_consumers(paths, code_ref="abc123")

    assert rows == []
    assert len(escalations) == 2


def test_resolve_close_surfaces_from_proof_payload() -> None:
    payload = {
        "close_surfaces": ["mcp_health"],
        "excluded_surfaces": [
            {
                "surface": "cortex_api",
                "import_path": "contradicted",
                "evidence_paths": ["libs/foo.py"],
            }
        ],
    }
    owed = resolve_close_surfaces(
        service="mcp",
        proof_class="client_visible",
        close_surfaces=None,
        proof_payload=payload,
    )
    assert owed == frozenset({"mcp_health"})


def test_excluded_surfaces_to_payload_roundtrip() -> None:
    from implement_admission.propagation_close_surfaces import (
        ExcludedSurface,
        excluded_surfaces_from_payload,
    )

    excluded = (
        ExcludedSurface(
            surface="cortex_api",
            import_path="contradicted",
            evidence_paths=("libs/a.py",),
        ),
    )
    payload = excluded_surfaces_to_payload(excluded)
    restored = excluded_surfaces_from_payload(payload)
    assert restored[0].surface == "cortex_api"
    assert restored[0].import_path == "contradicted"
