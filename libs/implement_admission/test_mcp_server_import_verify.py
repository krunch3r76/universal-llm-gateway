"""Unit tests for mcp-server import smoke at propagation mint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from implement_admission.mcp_server_import_verify import (
    is_mcp_runtime_module,
    is_mcp_server_path,
    mcp_server_import_smoke,
    module_for_mcp_path,
    modules_to_probe,
)


def test_module_for_mcp_path_tool_and_root() -> None:
    assert (
        module_for_mcp_path("services/mcp-server/tools/continuity.py")
        == "tools.continuity"
    )
    assert module_for_mcp_path("services/mcp-server/server.py") == "server"
    assert (
        module_for_mcp_path("services/mcp-server/tools/agent_bus/request.py")
        == "tools.agent_bus.request"
    )
    assert module_for_mcp_path("services/mcp-server/surface_registration.py") == (
        "surface_registration"
    )


def test_module_for_mcp_path_skips_tests() -> None:
    assert module_for_mcp_path("services/mcp-server/tools/test_foo.py") is None
    assert (
        module_for_mcp_path("services/mcp-server/tests/test_endpoint_surface_split.py")
        is None
    )
    assert module_for_mcp_path("services/mcp-server/conftest.py") is None


def test_modules_to_probe_dedupes_and_bootstraps_server() -> None:
    paths = [
        "services/mcp-server/tools/continuity.py",
        "services/mcp-server/tools/continuity.py",
        "services/mcp-server/server.py",
    ]
    assert modules_to_probe(paths) == ("tools.continuity", "server")
    assert modules_to_probe(["services/mcp-server/config/mcp.yaml"]) == ("server",)


def test_mcp_server_import_smoke_not_probed_without_mcp_paths() -> None:
    assert mcp_server_import_smoke(["services/git_integration_worker/x.py"]) == (
        "not_probed"
    )


@pytest.mark.offline
def test_mcp_server_import_smoke_verified_on_success(tmp_path: Path) -> None:
    mcp = tmp_path / "services" / "mcp-server"
    libs = tmp_path / "libs"
    mcp.mkdir(parents=True)
    libs.mkdir()
    (mcp / "server.py").write_text('"""ok."""\n', encoding="utf-8")

    with patch(
        "implement_admission.mcp_server_import_verify._run_import", return_value=True
    ) as run:
        status = mcp_server_import_smoke(["services/mcp-server/server.py"], root=tmp_path)
    assert status == "verified"
    run.assert_called_once()
    assert run.call_args.args[0] == "server"


@pytest.mark.offline
def test_mcp_server_import_smoke_contradicted_on_failed_import(tmp_path: Path) -> None:
    mcp = tmp_path / "services" / "mcp-server" / "tools"
    (tmp_path / "libs").mkdir()
    mcp.mkdir(parents=True)
    (mcp / "broken.py").write_text("import nonexistent\n", encoding="utf-8")

    with patch(
        "implement_admission.mcp_server_import_verify._run_import", return_value=False
    ):
        status = mcp_server_import_smoke(
            ["services/mcp-server/tools/broken.py"], root=tmp_path
        )
    assert status == "contradicted"


@pytest.mark.offline
def test_mcp_server_import_smoke_indeterminate_on_subprocess_error(
    tmp_path: Path,
) -> None:
    (tmp_path / "services" / "mcp-server").mkdir(parents=True)
    (tmp_path / "libs").mkdir()
    (tmp_path / "services" / "mcp-server" / "server.py").write_text("", encoding="utf-8")

    with patch(
        "implement_admission.mcp_server_import_verify._run_import",
        side_effect=OSError("spawn failed"),
    ):
        status = mcp_server_import_smoke(["services/mcp-server/server.py"], root=tmp_path)
    assert status == "indeterminate"


def test_is_mcp_server_path_and_runtime_module() -> None:
    assert is_mcp_server_path("services/mcp-server/server.py")
    assert not is_mcp_server_path("services/git_integration_worker/x.py")
    assert is_mcp_runtime_module("services/mcp-server/tools/manage.py")
    assert not is_mcp_runtime_module("services/mcp-server/tools/test_manage.py")
