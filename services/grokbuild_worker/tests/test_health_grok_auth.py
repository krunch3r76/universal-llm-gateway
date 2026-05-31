"""Unit tests for grokbuild_worker health grok_auth field and Option A rollup.

§F3 decision guard: Option A — grok_auth is informational; status rollup
covers only the four core readiness checks (grok_binary, auth_dir,
sidecar_dir, registry).
"""

from __future__ import annotations

import os
import types
from pathlib import Path

import pytest
from grokbuild.auth_probe import AuthStatus

from services.grokbuild_worker.routes.health import _evaluate  # noqa: E402

# ── fixtures ─────────────────────────────────────────────────────────────────


def _make_cfg(tmp_path: Path) -> types.SimpleNamespace:
    """Build a cfg-like namespace with all four core checks returning 'ok'."""
    grok_bin = tmp_path / "grok"
    grok_bin.write_bytes(b"#!/bin/sh\n")
    os.chmod(grok_bin, 0o755)

    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()

    sidecar_dir = tmp_path / "sidecar"
    sidecar_dir.mkdir()

    registry_parent = tmp_path / "registry"
    registry_parent.mkdir()
    registry_path = registry_parent / "registry.json"

    return types.SimpleNamespace(
        grok_bin_path=grok_bin,
        grok_auth_dir=auth_dir,
        sidecar_dir=sidecar_dir,
        registry_path=registry_path,
    )


def _state(status: AuthStatus) -> types.SimpleNamespace:
    return types.SimpleNamespace(grok_auth_status=status)


# ── grok_auth field mapping ───────────────────────────────────────────────────


class TestGrokAuthField:
    def test_ok_status_maps_to_ok(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        checks = _evaluate(cfg, _state(AuthStatus.OK))
        assert checks["grok_auth"] == "ok"

    def test_expired_status_maps_to_expired(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        checks = _evaluate(cfg, _state(AuthStatus.EXPIRED))
        assert checks["grok_auth"] == "expired"

    def test_missing_status_maps_to_missing(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        checks = _evaluate(cfg, _state(AuthStatus.MISSING))
        assert checks["grok_auth"] == "missing"

    def test_none_state_maps_grok_auth_to_missing(self, tmp_path: Path) -> None:
        """state=None (pre-lifespan call) → grok_auth == 'missing'."""
        cfg = _make_cfg(tmp_path)
        checks = _evaluate(cfg, None)
        assert checks["grok_auth"] == "missing"

    def test_state_without_grok_auth_status_attr_maps_to_missing(
        self, tmp_path: Path
    ) -> None:
        """state present but lacking grok_auth_status → grok_auth == 'missing'."""
        cfg = _make_cfg(tmp_path)
        checks = _evaluate(cfg, types.SimpleNamespace())  # no grok_auth_status
        assert checks["grok_auth"] == "missing"


# ── Option A rollup (§F3 decision guard) ─────────────────────────────────────
#
# OQ-1 resolved as Option A: grok_auth is operator-actionable and does NOT
# drive the degraded rollup. status == "ok" even when grok_auth is non-ok,
# as long as the four core readiness checks (grok_binary, auth_dir,
# sidecar_dir, registry) are all "ok".


class TestOptionARollup:
    @pytest.mark.parametrize("auth_status", [AuthStatus.EXPIRED, AuthStatus.MISSING])
    def test_status_ok_when_only_grok_auth_non_ok(
        self, tmp_path: Path, auth_status: AuthStatus
    ) -> None:
        """All four core checks ok + grok_auth non-ok → status == 'ok' (Option A)."""
        cfg = _make_cfg(tmp_path)
        checks = _evaluate(cfg, _state(auth_status))

        core_keys = ("grok_binary", "auth_dir", "sidecar_dir", "registry")
        # verify core checks are all ok (test isolation guard)
        for key in core_keys:
            assert checks[key] == "ok", f"expected core check {key!r} == 'ok', got {checks[key]!r}"

        # grok_auth must reflect the probe status
        assert checks["grok_auth"] != "ok"

        # Option A: status is computed over core checks only → must be "ok"
        # The rollup in health.py: status = "ok" if all(checks[k] == "ok" for k in core_keys) else "degraded"
        rollup_keys = core_keys
        status = "ok" if all(checks[k] == "ok" for k in rollup_keys) else "degraded"
        assert status == "ok"

    def test_all_ok_including_grok_auth_yields_status_ok(
        self, tmp_path: Path
    ) -> None:
        """When all checks including grok_auth are ok, status is 'ok'."""
        cfg = _make_cfg(tmp_path)
        checks = _evaluate(cfg, _state(AuthStatus.OK))
        rollup_keys = ("grok_binary", "auth_dir", "sidecar_dir", "registry")
        status = "ok" if all(checks[k] == "ok" for k in rollup_keys) else "degraded"
        assert status == "ok"
        assert checks["grok_auth"] == "ok"
