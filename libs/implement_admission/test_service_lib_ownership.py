"""Declared lib ownership manifest — audit and propagation resolution tests."""

from __future__ import annotations

import json

import pytest

from implement_admission.service_lib_ownership import (
    declared_services_for_lib_path,
    path_prefixes,
    service_ownership,
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


def test_inferred_fanout_ge_two_defers_without_restart(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.propagation_execute.declared_services_for_lib_path",
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
