"""Declared lib ownership manifest — audit and propagation resolution tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from implement_admission.consumer_import_blinds import measure_import_grammar_blinds
from implement_admission.consumer_import_verify import (
    _entrypoint_seeds,
    verify_consumer_import,
)
from implement_admission.restart_owed import restart_owed_line
from implement_admission.service_lib_ownership import (
    declared_services_for_lib,
    declared_services_for_lib_path,
    path_prefixes,
    service_ownership,
    serving_services_for_lib_path,
)
from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
    _resolve_libs_path,
    plan_propagation,
)
from scripts.model_manager.ui.controller.charter_runner.propagation_libs_closure import (
    _lib_to_services,
    repo_root,
)


def _closeout_turn(*, files_modified: list[str] | None = None) -> dict:
    body: dict = {"status": "complete", "evidence_uris": {"git_refs": ["land-sha"]}}
    if files_modified is not None:
        body["files_modified"] = files_modified
    return {"turn_number": 3, "body": json.dumps(body)}


def test_cortex_store_main_resolves_to_cortex_api_via_declared_manifest() -> None:
    plan = plan_propagation(
        [_closeout_turn(files_modified=["libs/cortex_store/main.py"])]
    )
    assert plan is not None
    assert "cortex_api" in plan.sync_restart_services


def test_wait_status_plan_nominates_agent_bus_not_seven_owners() -> None:
    """Charter/harvest plan for 33083d61 must include agent_bus and not owned_libs."""
    from implement_admission.service_lib_ownership import declared_services_for_lib

    plan = plan_propagation(
        [_closeout_turn(files_modified=["libs/agent_bus_store/wait_status.py"])]
    )
    assert plan is not None
    services = set(plan.sync_restart_services)
    owned = set(declared_services_for_lib("agent_bus_store"))
    assert "agent_bus" in services
    assert services <= {"agent_bus", "mcp"}
    assert not owned <= services


def test_inferred_fanout_ge_two_defers_without_restart(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.propagation_execute.serving_services_for_lib_path",
        lambda _path: (),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.propagation_execute.services_for_lib_path",
        lambda _path, *, prefixes: ("agent_bus", "cortex_api"),
    )
    slugs, deferrals = _resolve_libs_path("libs/shared_example/foo.py")
    assert slugs == ()
    assert len(deferrals) == 1
    assert "fans out to agent_bus, cortex_api" in deferrals[0]


@pytest.mark.offline
def test_declared_superset_of_measured_closure_per_service() -> None:
    measured = _lib_to_services(str(repo_root()), path_prefixes())
    measured_by_service: dict[str, set[str]] = {}
    for lib, slugs in measured.items():
        for slug in slugs:
            measured_by_service.setdefault(slug, set()).add(lib)

    for slug, own in service_ownership().items():
        missing = measured_by_service.get(slug, set()) - own.owned_libs
        assert not missing, f"{slug}: declared manifest missing {sorted(missing)}"


@pytest.mark.offline
def test_audit_fails_when_declared_entry_removed() -> None:
    import implement_admission.service_lib_ownership as manifest

    original = manifest._SERVICE_OWNERSHIP["cortex_api"]
    trimmed = manifest.ServiceOwnership(
        path_prefix=original.path_prefix,
        owned_libs=original.owned_libs - {"cortex_store"},
    )
    manifest._SERVICE_OWNERSHIP["cortex_api"] = trimmed
    try:
        with pytest.raises(AssertionError, match="cortex_store"):
            test_declared_superset_of_measured_closure_per_service()
    finally:
        manifest._SERVICE_OWNERSHIP["cortex_api"] = original


def test_declared_cortex_store_path() -> None:
    owners = declared_services_for_lib_path("libs/cortex_store/main.py")
    assert "cortex_api" in owners


def test_giw_serves_worker_hosted_libs_not_owned_libs() -> None:
    """G1 §7 census: GIW job set is not a drop-in of owned_libs."""
    own = service_ownership()["git_integration_worker"]
    assert own.serves_libs == frozenset(
        {
            "charter_runner_store",
            "consult_substrate_notice",
            "git_integrate",
            "implement_admission",
            "job_grammar",
            "job_vocab",
            "prompt_expand_consume",
            "work_key_grammar",
        }
    )
    assert own.serves_libs < own.owned_libs
    assert "agent_bus_store" not in own.serves_libs
    assert "foo" not in own.serves_libs


def test_wait_status_serving_slug_is_agent_bus_not_owned_libs_blast() -> None:
    """Replay 33083d61: serving-process set is {agent_bus}, not the seven owners."""
    serving = serving_services_for_lib_path("libs/agent_bus_store/wait_status.py")
    owned = set(declared_services_for_lib("agent_bus_store"))
    assert serving == ("agent_bus",)
    assert owned == {
        "agent_bus",
        "cloud_proxy",
        "cortex_api",
        "git_integration_worker",
        "mcp",
        "rag",
        "stargate",
    }
    assert not owned <= set(serving)


@pytest.mark.offline
def test_event_service_runtime_entrypoint_verifies_event_store() -> None:
    """Empty services/event-service/ tree; reach comes from runtime_entrypoint.

    Package __init__ is reached whenever a submodule is. store.py is loaded by
    event_store.server, so a broken walk from __main__ fails this assertion.
    query_client.py is not on that walk: serves_libs is package-granular, so
    the closeout still restarts event_service (operator S3) while file verify
    stays contradicted.
    """
    assert (
        verify_consumer_import("event_service", "libs/event_store/__init__.py")
        == "verified"
    )
    assert (
        verify_consumer_import("event_service", "libs/event_store/store.py")
        == "verified"
    )
    assert (
        verify_consumer_import("event_service", "libs/event_store/query_client.py")
        == "contradicted"
    )
    assert (
        restart_owed_line(["libs/event_store/query_client.py"])
        == "restart_owed: event_service"
    )


@pytest.mark.offline
def test_missing_runtime_entrypoint_seeds_nothing(tmp_path: Path) -> None:
    """A runtime_entrypoint path that is not on disk adds no modules."""
    assert _entrypoint_seeds(tmp_path, "libs/missing_pkg/__main__.py") == set()


@pytest.mark.offline
def test_directory_entrypoint_seeds_imports_not_every_file(tmp_path: Path) -> None:
    """Directory entrypoints must not mark unused siblings reached."""
    pkg = tmp_path / "libs" / "sample_pkg"
    pkg.mkdir(parents=True)
    (pkg / "used.py").write_text("import event_store.store\n", encoding="utf-8")
    (pkg / "unused.py").write_text("x = 1\n", encoding="utf-8")
    seeds = _entrypoint_seeds(tmp_path, "libs/sample_pkg")
    assert "event_store.store" in seeds
    assert "sample_pkg.unused" not in seeds
    assert "sample_pkg.used" not in seeds


@pytest.mark.offline
def test_event_service_entrypoint_is_measured_for_blinds() -> None:
    """Relative imports in __main__.py are a blind, not an empty measurement."""
    blinds = measure_import_grammar_blinds("event_service", str(repo_root()))
    assert "service_relative" in blinds


@pytest.mark.offline
def test_serves_libs_entries_reach_their_package() -> None:
    """Authorship gate: every serves_libs pair must import-verify the package."""
    root = repo_root()
    for slug, own in service_ownership().items():
        for lib in sorted(own.serves_libs):
            init = root / "libs" / lib / "__init__.py"
            path = f"libs/{lib}/__init__.py" if init.is_file() else f"libs/{lib}.py"
            assert verify_consumer_import(slug, path) == "verified", (slug, lib, path)
