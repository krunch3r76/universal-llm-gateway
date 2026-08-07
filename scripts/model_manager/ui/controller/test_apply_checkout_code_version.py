"""Tests for edge compose ULG_CODE_VERSION sealing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.model_manager.ui.controller.service_config import apply_checkout_code_version


def test_apply_checkout_code_version_seals_head(monkeypatch):
    env: dict[str, str] = {}
    sha = "c" * 40
    with patch(
        "deploy_identity.code_version.read_checkout_head",
        return_value=sha,
    ):
        apply_checkout_code_version(env, Path("/repo"))
    assert env["ULG_CODE_VERSION"] == sha


def test_apply_checkout_code_version_replaces_empty_override(monkeypatch):
    env = {"ULG_CODE_VERSION": ""}
    sha = "d" * 40
    with patch(
        "deploy_identity.code_version.read_checkout_head",
        return_value=sha,
    ):
        apply_checkout_code_version(env, Path("/repo"))
    assert env["ULG_CODE_VERSION"] == sha


def test_apply_checkout_code_version_raises_when_head_unavailable():
    env: dict[str, str] = {}
    with patch(
        "deploy_identity.code_version.read_checkout_head",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="Cannot seal ULG_CODE_VERSION"):
            apply_checkout_code_version(env, Path("/repo"))
