"""Unit tests for _recover_root_owned_socket_dir and emit_relay_socket_recovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.model_manager.observation_event import emit_relay_socket_recovery
from scripts.model_manager.ui.controller.service_config import (
    _recover_root_owned_socket_dir,
)

_MODULE = "scripts.model_manager.ui.controller.service_config"


class TestRecoverRootOwnedSocketDir:
    def test_docker_cleanup_success(self, tmp_path: Path) -> None:
        socket_dir = tmp_path / "universal-protocol"
        socket_dir.mkdir()

        docker_result = MagicMock()
        docker_result.returncode = 0

        with patch("subprocess.run", return_value=docker_result) as mock_run:
            result = _recover_root_owned_socket_dir(socket_dir)

        assert result is True
        docker_args = mock_run.call_args_list[0].args[0]
        assert docker_args[0:2] == ["docker", "run"]

    def test_docker_cleanup_failure_then_user_rmtree(self, tmp_path: Path) -> None:
        socket_dir = tmp_path / "universal-protocol"
        socket_dir.mkdir()

        docker_result = MagicMock()
        docker_result.returncode = 1
        docker_result.stderr = "failed"

        with (
            patch("subprocess.run", return_value=docker_result),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            result = _recover_root_owned_socket_dir(socket_dir)

        assert result is True
        mock_rmtree.assert_called_once_with(socket_dir)

    def test_docker_and_user_cleanup_both_fail(self, tmp_path: Path) -> None:
        socket_dir = tmp_path / "universal-protocol"
        socket_dir.mkdir()

        docker_result = MagicMock()
        docker_result.returncode = 1

        with (
            patch("subprocess.run", return_value=docker_result),
            patch("shutil.rmtree", side_effect=OSError("permission denied")),
        ):
            result = _recover_root_owned_socket_dir(socket_dir)

        assert result is False


class TestEmitRelaySocketRecovery:
    def test_never_raises_on_emit_failure(self, tmp_path: Path) -> None:
        socket_dir = tmp_path / "universal-protocol"

        with patch(
            f"{_MODULE}._emit_sync",
            side_effect=RuntimeError("socket gone"),
            create=True,
        ):
            with patch(
                "scripts.model_manager.observation_event._emit_sync",
                side_effect=RuntimeError("socket gone"),
            ):
                try:
                    emit_relay_socket_recovery(
                        socket_dir=str(socket_dir),
                        owner_uid=0,
                        recovered=True,
                    )
                except Exception as exc:
                    pytest.fail(f"emit_relay_socket_recovery raised: {exc}")

    def test_never_raises_on_import_error(self, tmp_path: Path) -> None:
        socket_dir = tmp_path / "universal-protocol"

        with patch.dict(
            "sys.modules", {"scripts.model_manager.observation_event": None}
        ):
            try:
                emit_relay_socket_recovery(
                    socket_dir=str(socket_dir),
                    owner_uid=0,
                    recovered=True,
                )
            except Exception as exc:
                pytest.fail(f"emit_relay_socket_recovery raised on import error: {exc}")
