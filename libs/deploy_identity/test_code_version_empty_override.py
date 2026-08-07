"""Tests for loud rejection of present-but-empty ULG_CODE_VERSION overrides."""

from __future__ import annotations

import logging

import pytest

from deploy_identity.code_version import (
    _UNKNOWN,
    reset_code_version_cache_for_tests,
    resolve_code_version,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_code_version_cache_for_tests()
    monkeypatch.delenv("ULG_CODE_VERSION", raising=False)
    yield
    reset_code_version_cache_for_tests()


def test_empty_present_override_logs_error_and_does_not_silently_pass(
    monkeypatch, caplog
):
    """AC4: a present-but-empty ULG_CODE_VERSION must log at error, not hide."""
    monkeypatch.setenv("ULG_CODE_VERSION", "")
    caplog.set_level(logging.ERROR, logger="deploy_identity.code_version")
    reset_code_version_cache_for_tests()
    result = resolve_code_version()
    assert result == _UNKNOWN
    assert any(
        "present but empty" in record.message for record in caplog.records
    ), caplog.records


def test_malformed_present_override_logs_error(monkeypatch, caplog):
    reset_code_version_cache_for_tests()
    monkeypatch.setenv("ULG_CODE_VERSION", "not-a-sha")
    caplog.set_level(logging.ERROR, logger="deploy_identity.code_version")
    assert resolve_code_version() == _UNKNOWN
    assert any(
        "not a valid 40-hex SHA" in record.message for record in caplog.records
    )
