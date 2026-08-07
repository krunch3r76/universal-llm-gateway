"""Shared fixtures for charter_runner_store tests.

Most ledger tests use synthetic 40-char hex that is not in the git object DB.
Mint now requires ``resolve_commit_sha``; this autouse fixture treats full
hex as self-resolving unless the test opts into real git via
``@pytest.mark.real_git_resolve``.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_git_resolve: use real git rev-parse (disable synthetic 40-hex resolve)",
    )


@pytest.fixture(autouse=True)
def _synthetic_code_ref_resolve(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
):
    if request.node.get_closest_marker("real_git_resolve") is not None:
        return

    from deploy_identity import code_ref_relation as relation_mod

    real = relation_mod.resolve_commit_sha

    def _fake(value: str) -> str | None:
        normalized = str(value or "").strip().lower()
        if len(normalized) == 40 and all(
            char in "0123456789abcdef" for char in normalized
        ):
            return normalized
        return real(value)

    monkeypatch.setattr(relation_mod, "resolve_commit_sha", _fake)
    monkeypatch.setattr(relation_mod, "_resolve_commit_sha", _fake)
