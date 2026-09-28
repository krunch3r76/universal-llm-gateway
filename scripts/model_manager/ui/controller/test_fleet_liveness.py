"""Unit coverage for load-surface-specific fleet liveness evidence."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from scripts.model_manager.ui.controller import fleet_liveness as live
from scripts.model_manager.ui.controller import fleet_liveness_probe as probe
from scripts.model_manager.ui.model.service_state import ServiceInfo, ServiceStatus


def _path_row(*, service: str, status: str = " M") -> dict:
    return {
        "path": "services/mcp-server/tools/fleet_liveness.py",
        "status": status,
        "mtime_ns": 1_700_000_000_000_000_000,
        "serving_services": [service],
    }


def test_container_hash_compares_running_bytes_to_claimed_blob(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(live, "_container_sha", lambda *_: "same-sha")
    monkeypatch.setattr(live, "_git_blob_sha", lambda *_: "same-sha")
    result = live._path_comparison(
        tmp_path,
        service="mcp",
        path_row=_path_row(service="mcp"),
        head_sha="head",
        marker={"value_utc": None},
        reported={"value": "reported"},
        tree_moved=False,
    )
    assert result["comparison_method"] == "content_hash_in_load_location"
    assert result["running_bytes_determinable"] == "yes"
    assert result["matches_reported_sha"] == "yes"


def test_untracked_container_path_is_definite_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(live, "_container_sha", lambda *_: "running")
    monkeypatch.setattr(live, "_git_blob_sha", lambda *_: None)
    result = live._path_comparison(
        tmp_path,
        service="mcp",
        path_row=_path_row(service="mcp", status="??"),
        head_sha="head",
        marker={"value_utc": None},
        reported={"value": "reported"},
        tree_moved=False,
    )
    assert result["matches_reported_sha"] == "no"
    assert result["indeterminate_reason"] == "untracked_no_blob"


def test_container_hash_failure_does_not_fall_back_to_time_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(live, "_container_sha", lambda *_: None)
    monkeypatch.setattr(live, "_git_blob_sha", lambda *_: "claimed")
    result = live._path_comparison(
        tmp_path,
        service="mcp",
        path_row=_path_row(service="mcp"),
        head_sha="head",
        marker={"value_utc": "2026-08-15T00:00:00Z"},
        reported={"value": "reported"},
        tree_moved=False,
    )
    assert result["comparison_method"] == "content_hash_in_load_location"
    assert result["matches_reported_sha"] == "indeterminate"
    assert result["indeterminate_reason"] == "probe_error"


def test_host_time_order_does_not_claim_execution(tmp_path) -> None:
    marker_time = datetime.fromtimestamp(1_700_000_100, UTC)
    result = live._path_comparison(
        tmp_path,
        service="stargate",
        path_row={
            "path": "libs/example.py",
            "status": " M",
            "mtime_ns": 1_700_000_000_000_000_000,
            "serving_services": ["stargate"],
        },
        head_sha="head",
        marker={
            "value_utc": marker_time.isoformat().replace("+00:00", "Z"),
            "granularity_s": 0.001,
        },
        reported={"value": None},
        tree_moved=False,
    )
    assert result["comparison_method"] == "mtime_vs_load_marker"
    assert result["matches_reported_sha"] == "indeterminate"
    assert result["indeterminate_reason"] == "host_process_import_not_observable"


def test_bind_mount_is_indeterminate_even_with_marker() -> None:
    result = live._path_comparison(
        live.Path("."),
        service="gateway",
        path_row=_path_row(service="gateway"),
        head_sha="head",
        marker={"value_utc": "2026-08-15T00:00:00Z"},
        reported={"value": "head"},
        tree_moved=False,
    )
    assert result["comparison_method"] == "bind_mount_module_import_unmeasured"
    assert result["indeterminate_reason"] == "bind_mount_per_module_import"


def test_shared_library_join_preserves_multiple_serving_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        probe,
        "serving_services_for_lib_path",
        lambda _path: ("agent_bus", "stargate"),
    )
    services = probe.services_for_path("libs/shared/runtime.py")
    assert services == ("agent_bus", "stargate")


def test_deleted_path_keeps_mtime_unknown_and_reports_probe_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        probe,
        "git",
        lambda _root, *args: "master" if args[0] == "symbolic-ref" else "head",
    )
    monkeypatch.setattr(
        probe,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout=" D services/universal-stargate/app.py\0",
            stderr="",
        ),
    )
    result = probe.tree_probe(tmp_path)
    path = result["paths"]["services/universal-stargate/app.py"]
    assert path["mtime_ns"] is None
    assert path["probe_error"] == "deleted_path"
    assert "services/universal-stargate/app.py:deleted_path" in result["errors"]


def test_clock_observation_exposes_boot_granularity_and_step() -> None:
    result = probe.clock_observation()
    assert result["domain"] == "host_wall_clock"
    assert result["granularity_s"] > 0
    assert result["step_ns"] >= 0
    assert result["boot_utc"]


def test_snapshot_marks_tree_motion(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    open_probe = {
        "raw": " M libs/example.py\0",
        "paths": {
            "libs/example.py": {
                "path": "libs/example.py",
                "status": " M",
                "mtime_ns": 1,
                "serving_services": ["stargate"],
                "on_load_surface": True,
            }
        },
        "errors": [],
        "branch": "master",
        "head_sha": "head",
    }
    close_probe = {**open_probe, "raw": " M libs/example.py\0"}
    close_probe["paths"] = {
        **open_probe["paths"],
        "libs/example.py": {**open_probe["paths"]["libs/example.py"], "mtime_ns": 2},
    }
    probes = iter((open_probe, close_probe))
    monkeypatch.setattr(live, "_tree_probe", lambda *_: next(probes))
    monkeypatch.setattr(
        live,
        "_service_info",
        lambda _state, service: ServiceInfo(
            name=service, status=ServiceStatus.RUNNING, pid=123
        ),
    )
    monkeypatch.setattr(
        live,
        "_container_start",
        lambda _container: {
            "kind": "container_started_at",
            "value_utc": None,
            "granularity_s": 0.001,
            "clock_domain": "docker_host",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_mcp_reported_version",
        lambda _container: {
            "field": "code_version",
            "value": None,
            "denotes": "test",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_process_start",
        lambda _pid: {
            "kind": "host_proc_start",
            "value_utc": "2026-08-15T00:00:00Z",
            "granularity_s": 0.001,
            "clock_domain": "host_proc",
            "error": None,
        },
    )
    result = live.build_snapshot(tmp_path, SimpleNamespace())
    assert result["tree_moved_during_probe"] is True
    path = next(
        item
        for row in result["services"]
        for item in row["paths"]
        if item["path"] == "libs/example.py"
    )
    assert path["indeterminate_reason"] == "tree_moved_during_probe"


def test_snapshot_copies_detail_pid_health_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """fleet_liveness must disclose the checker's already-known fail classes."""
    empty_probe = {
        "raw": "",
        "paths": {},
        "errors": [],
        "branch": "master",
        "head_sha": "head",
        "clock": {},
    }
    probes = iter((empty_probe, empty_probe))
    monkeypatch.setattr(live, "_tree_probe", lambda *_: next(probes))
    monkeypatch.setattr(
        live,
        "_service_info",
        lambda _state, service: (
            ServiceInfo(
                name="RAG",
                status=ServiceStatus.UNHEALTHY,
                pid=3471126,
                health_url="unix:///tmp/universal-protocol/rag.sock/stats",
                detail="PID 3471126 (17h 42m), probe failed (TimeoutError)",
                fail_class="timeout",
            )
            if service == "rag"
            else ServiceInfo(name=service, status=ServiceStatus.RUNNING, pid=1)
        ),
    )
    monkeypatch.setattr(
        live,
        "_container_start",
        lambda _container: {
            "kind": "container_started_at",
            "value_utc": None,
            "granularity_s": 0.001,
            "clock_domain": "docker_host",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_mcp_reported_version",
        lambda _container: {
            "field": "code_version",
            "value": None,
            "denotes": "test",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_process_start",
        lambda _pid: {
            "kind": "host_proc_start",
            "value_utc": "2026-08-14T22:20:54Z",
            "granularity_s": 0.01,
            "clock_domain": "host_proc",
            "error": None,
        },
    )
    result = live.build_snapshot(tmp_path, SimpleNamespace())
    rag = next(row for row in result["services"] if row["service"] == "rag")
    assert rag["status"] == "unhealthy"
    assert rag["detail"] == "PID 3471126 (17h 42m), probe failed (TimeoutError)"
    assert rag["pid"] == 3471126
    assert rag["health_url"] == "unix:///tmp/universal-protocol/rag.sock/stats"
    assert rag["fail_class"] == "timeout"


def test_omitting_activation_validation_id_preserves_join_miss_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Omitting activation_validation_id must not widen the primary join."""
    empty_probe = {
        "raw": "",
        "paths": {},
        "errors": [],
        "branch": "master",
        "head_sha": "head",
        "clock": {},
    }
    probes = iter((empty_probe, empty_probe))
    monkeypatch.setattr(live, "_tree_probe", lambda *_: next(probes))
    monkeypatch.setattr(
        live,
        "_service_info",
        lambda _state, service: ServiceInfo(
            name=service, status=ServiceStatus.RUNNING, pid=1
        ),
    )
    monkeypatch.setattr(
        live,
        "_container_start",
        lambda _container: {
            "kind": "container_started_at",
            "value_utc": None,
            "granularity_s": 0.001,
            "clock_domain": "docker_host",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_mcp_reported_version",
        lambda _container: {
            "field": "code_version",
            "value": None,
            "denotes": "test",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_process_start",
        lambda _pid: {
            "kind": "host_proc_start",
            "value_utc": "2026-08-15T00:00:00Z",
            "granularity_s": 0.001,
            "clock_domain": "host_proc",
            "error": None,
        },
    )
    calls: list[dict[str, object | None]] = []

    def _current_validation(service, code_ref, *, activation_validation_id=None):
        calls.append(
            {
                "service": service,
                "code_ref": code_ref,
                "activation_validation_id": activation_validation_id,
            }
        )
        return {
            "verdict": "activation_unattributed",
            "liveness": {"answer": "yes", "observation": {}},
            "activation": None,
        }

    monkeypatch.setattr(live, "current_validation", _current_validation)
    result = live.build_snapshot(tmp_path, SimpleNamespace(), code_ref="probe-sha")
    giw = next(
        row for row in result["services"] if row["service"] == "git_integration_worker"
    )
    assert giw["code_ref_validation"]["verdict"] == "activation_unattributed"
    assert giw["code_ref_validation"]["activation"] is None
    giw_call = next(c for c in calls if c["service"] == "git_integration_worker")
    assert giw_call == {
        "service": "git_integration_worker",
        "code_ref": "probe-sha",
        "activation_validation_id": None,
    }


def test_code_ref_validation_labeled_derived_under_manage_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """code_ref liveness must not read as contradicting services[].status."""
    empty_probe = {
        "raw": "",
        "paths": {},
        "errors": [],
        "branch": "master",
        "head_sha": "head",
        "clock": {},
    }
    probes = iter((empty_probe, empty_probe))
    monkeypatch.setattr(live, "_tree_probe", lambda *_: next(probes))
    monkeypatch.setattr(
        live,
        "_service_info",
        lambda _state, service: (
            ServiceInfo(
                name="git-integration-worker",
                status=ServiceStatus.UNHEALTHY,
                pid=123,
                health_url="http://127.0.0.1:8791/health",
                detail="PID 123, health probe failed",
            )
            if service == "git_integration_worker"
            else ServiceInfo(name=service, status=ServiceStatus.RUNNING, pid=1)
        ),
    )
    monkeypatch.setattr(
        live,
        "_container_start",
        lambda _container: {
            "kind": "container_started_at",
            "value_utc": None,
            "granularity_s": 0.001,
            "clock_domain": "docker_host",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_mcp_reported_version",
        lambda _container: {
            "field": "code_version",
            "value": None,
            "denotes": "test",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_process_start",
        lambda _pid: {
            "kind": "host_proc_start",
            "value_utc": "2026-08-15T00:00:00Z",
            "granularity_s": 0.001,
            "clock_domain": "host_proc",
            "error": None,
        },
    )
    monkeypatch.setattr(
        live,
        "current_validation",
        lambda service, code_ref, activation_validation_id=None: {
            "verdict": "running_committed_code",
            "liveness": {
                "answer": "yes",
                "observation": {"live": True, "probe_reachable": True},
            },
            "activation": None,
        },
    )
    result = live.build_snapshot(tmp_path, SimpleNamespace(), code_ref="abc123")
    giw = next(
        row for row in result["services"] if row["service"] == "git_integration_worker"
    )
    assert giw["status"] == "unhealthy"
    prov = giw["code_ref_validation"]["provenance"]
    assert prov["authority"] == "services[].status"
    assert prov["basis"] == "derived"
    assert prov["contradiction"]["manage_status"] == "unhealthy"
    assert prov["contradiction"]["code_ref_live"] is True


def test_mcp_reported_version_qualifies_working_tree_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stamp = (
        "2026-08-15T09:30:00Z\n"
        "a28906fc180db15916f61f00eb61a633b7781689\n"
        "source_basis=working_tree\n"
        "code_version_semantics=checkout_head_at_source_sync\n"
        "working_tree_state=dirty\n"
    )
    monkeypatch.setattr(
        probe,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stamp, stderr=""
        ),
    )

    assert probe.mcp_reported_version("mcp-server") == {
        "field": "code_version",
        "value": "a28906fc180db15916f61f00eb61a633b7781689",
        "source": "docker:/app/.source_sync_stamp",
        "denotes": "checkout_head_at_source_sync",
        "source_sync_basis": "working_tree",
        "source_sync_worktree_state": "dirty",
        "error": None,
    }


def _git(repo, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def test_index_behind_head_when_ref_moves_without_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A ref-only advance of an on-load-surface path is index_behind_head."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    path = repo / "services" / "git_integration_worker" / "stale_probe.py"
    path.parent.mkdir(parents=True)
    path.write_text("old\n", encoding="utf-8")
    _git(repo, "add", "services/git_integration_worker/stale_probe.py")
    _git(repo, "commit", "-m", "old")
    _git(repo, "checkout", "-b", "lane")
    path.write_text("new\n", encoding="utf-8")
    _git(repo, "add", "services/git_integration_worker/stale_probe.py")
    _git(repo, "commit", "-m", "new")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "master")
    _git(repo, "update-ref", "refs/heads/master", head)
    assert path.read_text(encoding="utf-8") == "old\n"

    monkeypatch.setattr(
        live,
        "_service_info",
        lambda _state, service: ServiceInfo(
            name=service, status=ServiceStatus.RUNNING, pid=123
        ),
    )
    monkeypatch.setattr(
        live,
        "_container_start",
        lambda _container: {
            "kind": "container_started_at",
            "value_utc": None,
            "granularity_s": 0.001,
            "clock_domain": "docker_host",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_mcp_reported_version",
        lambda _container: {
            "field": "code_version",
            "value": None,
            "denotes": "test",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_process_start",
        lambda _pid: {
            "kind": "host_proc_start",
            "value_utc": "2026-08-15T00:00:00Z",
            "granularity_s": 0.001,
            "clock_domain": "host_proc",
            "error": None,
        },
    )
    result = live.build_snapshot(repo, SimpleNamespace())
    checkout = result["checkout"]
    print("CHECKOUT_VERDICT=" + str(checkout["verdict"]))
    print("INDEX_BEHIND_PATHS=" + str(checkout["index_behind_head_paths"]))
    print("CHECKOUT_HEAD=" + str(checkout["head_sha"]))
    assert checkout["verdict"] == "index_behind_head"
    assert (
        "services/git_integration_worker/stale_probe.py"
        in checkout["index_behind_head_paths"]
    )
    assert path.read_text(encoding="utf-8") == "old\n"


def test_defect_b_cdp_ask_specimen_does_not_read_remote_pid_in_local_proc(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Sidecar specimen 2, same fleet_liveness sample 2026-09-28T10:56:40.965798Z.

    cdp_ask probe_reachable was true with pid 3000629 from
    http://10.0.0.76:8770/health while load_marker.error was
    proc_start_unavailable. That pid is not on this host; the row must say
    so without consulting local /proc.
    """
    empty_probe = {
        "raw": "",
        "paths": {},
        "errors": [],
        "branch": "master",
        "head_sha": "head",
        "clock": {},
    }
    probes = iter((empty_probe, empty_probe))
    monkeypatch.setattr(live, "_tree_probe", lambda *_: next(probes))
    specimen_pid = 3000629
    specimen_url = "http://10.0.0.76:8770/health"
    local_pid = 1050900

    def _info(_state: object, service: str) -> ServiceInfo:
        if service == "cdp_ask":
            return ServiceInfo(
                name="cdp-ask",
                status=ServiceStatus.RUNNING,
                pid=specimen_pid,
                health_url=specimen_url,
                detail="PID 3000629; registry_hygiene=running",
            )
        if service == "agent_bus":
            return ServiceInfo(
                name="agent-bus",
                status=ServiceStatus.RUNNING,
                pid=local_pid,
                health_url="unix:///tmp/universal-protocol/agent-bus.sock/health",
                detail="PID 1050900",
            )
        return ServiceInfo(name=service, status=ServiceStatus.RUNNING, pid=1)

    monkeypatch.setattr(live, "_service_info", _info)
    monkeypatch.setattr(
        live,
        "_container_start",
        lambda _container: {
            "kind": "container_started_at",
            "value_utc": None,
            "granularity_s": 0.001,
            "clock_domain": "docker_host",
            "error": "test",
        },
    )
    monkeypatch.setattr(
        live,
        "_mcp_reported_version",
        lambda _container: {
            "field": "code_version",
            "value": None,
            "denotes": "test",
            "error": "test",
        },
    )
    consulted: list[int | None] = []

    def _start(pid: int | None) -> dict[str, object]:
        consulted.append(pid)
        return {
            "kind": "host_proc_start",
            "value_utc": None,
            "granularity_s": 0.01,
            "clock_domain": "host_proc",
            "error": "proc_start_unavailable",
        }

    monkeypatch.setattr(live, "_process_start", _start)
    result = live.build_snapshot(tmp_path, SimpleNamespace())
    cdp = next(row for row in result["services"] if row["service"] == "cdp_ask")
    assert cdp["status"] == "running"
    assert cdp["pid"] == specimen_pid
    assert specimen_pid not in consulted
    marker = cdp["load_marker"]
    assert marker["value_utc"] is None
    assert marker["error"] == (
        f"pid {specimen_pid} reported by {specimen_url} is not on this host; "
        "local /proc was not read"
    )
    assert marker["error"] != "proc_start_unavailable"
    assert local_pid in consulted
