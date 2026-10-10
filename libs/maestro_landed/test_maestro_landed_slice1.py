"""AC1–AC7 and falsifiers for maestro-landed-live slice 1."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from maestro_landed.disposition import draft_disposition
from maestro_landed.manage_allowlist import refuse_manage_call, trace_allowed
from maestro_landed.manage_client import ManageClient
from maestro_landed.restart_plan import compute_dry_run
from maestro_landed.serving_map import load_serving_map
from maestro_landed.source_pins import verify_source_pins

REPO = Path(__file__).resolve().parents[2]


def _pin_verifier_always_ok(
    _pins: dict[str, Any], *, files_root: Path | None = None
) -> tuple[bool, dict[str, str]]:
    del files_root
    return True, {}
LAND_4ACC8E10 = "4acc8e10"


def _paths_4acc8e10_store_only() -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(REPO), "show", LAND_4ACC8E10, "--name-only", "--format="],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip().startswith("libs/agent_bus_store/")
    ]


class FakeManage(ManageClient):
    def __init__(
        self,
        *,
        liveness_snapshot: dict[str, Any] | None = None,
        giw_busy: bool = False,
    ) -> None:
        super().__init__()
        self._liveness = liveness_snapshot or {"services": []}
        self._giw_busy = giw_busy

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        reason = refuse_manage_call(method, params)
        if reason:
            return {"error": reason, "refused": True}
        self.trace.append({"method": method, "params": dict(params)})
        if method == "fleet_liveness":
            return dict(self._liveness)
        if method == "busy_status" and params.get("service") == "git_integration_worker":
            return {"busy": self._giw_busy, "state": "busy" if self._giw_busy else "idle"}
        if method in ("status", "health"):
            return {"ok": True}
        return {"error": f"unexpected fake method {method}"}


@pytest.mark.offline
def test_ac1_replay_4acc8e10_superset_agent_bus_mcp_empty_propagation() -> None:
    touched = _paths_4acc8e10_store_only()
    assert touched, "expected libs/agent_bus_store paths on 4acc8e10"
    manage = FakeManage(liveness_snapshot={"services": []})
    result = compute_dry_run(
        land_sha=LAND_4ACC8E10,
        touched_paths=touched,
        porcelain_raw="",
        serving_map=load_serving_map(),
        manage=manage,
        propagation=[],
        pin_verifier=_pin_verifier_always_ok,
    )
    assert result.status == "OK"
    assert set(result.restart_set) >= {"agent_bus", "mcp"}


@pytest.mark.offline
def test_ac2_porcelain_m_on_touched_stops_empty_restart_set() -> None:
    touched = ["libs/agent_bus_store/foo.py"]
    porcelain = " M libs/agent_bus_store/foo.py\n"
    manage = FakeManage()
    result = compute_dry_run(
        land_sha="abc123",
        touched_paths=touched,
        porcelain_raw=porcelain,
        serving_map=load_serving_map(),
        manage=manage,
        pin_verifier=_pin_verifier_always_ok,
    )
    assert result.status == "STOP"
    assert result.restart_set == []
    assert result.porcelain_block == ["libs/agent_bus_store/foo.py"]


@pytest.mark.offline
def test_ac3_trace_allowlist_only_readonly_manage() -> None:
    touched = _paths_4acc8e10_store_only()
    manage = FakeManage(liveness_snapshot={"services": []})
    compute_dry_run(
        land_sha=LAND_4ACC8E10,
        touched_paths=touched,
        porcelain_raw="",
        serving_map=load_serving_map(),
        manage=manage,
        pin_verifier=_pin_verifier_always_ok,
    )
    assert trace_allowed(manage.trace)
    methods = {t["method"] for t in manage.trace}
    assert methods <= {"fleet_liveness", "busy_status", "status", "health"}


@pytest.mark.offline
def test_ac4_disposition_draft_within_2000_with_r9_fields() -> None:
    text, chars, within = draft_disposition(
        land_sha=LAND_4ACC8E10,
        restart_set=["agent_bus", "mcp"],
    )
    assert within
    assert chars <= 2000
    assert "R9 route:" in text
    assert "R9 wall_time:" in text
    assert "R9 nested_usage:" in text


@pytest.mark.offline
def test_ac6_source_pin_refuse_on_drift(tmp_path: Path) -> None:
    serving_map = load_serving_map()
    root = tmp_path / "files"
    for spec in serving_map.source_pins.values():
        rel = str(spec["uri"]).removeprefix("cortex://")
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("drifted content\n", encoding="utf-8")
    ok, details = verify_source_pins(serving_map.source_pins, files_root=root)
    assert not ok
    manage = FakeManage()
    result = compute_dry_run(
        land_sha=LAND_4ACC8E10,
        touched_paths=["libs/agent_bus_store/x.py"],
        porcelain_raw="",
        serving_map=serving_map,
        manage=manage,
        files_root=root,
    )
    assert not result.source_pins_ok
    assert result.restart_set == []


@pytest.mark.offline
def test_ac6_live_pins_match_when_cortex_root_available() -> None:
    serving_map = load_serving_map()
    ok, _details = verify_source_pins(serving_map.source_pins)
    if not Path("/mnt/torus/mcp-data/files/notes/runbooks/maestro-loop.md").is_file():
        pytest.skip("CORTEX_FILES_ROOT not available in this environment")
    assert ok


@pytest.mark.offline
@pytest.mark.parametrize(
    "method,params,expect_refused",
    [
        ("sync_restart", {"service": "agent_bus", "force": True}, True),
        (
            "sync_restart",
            {"service": "git_integration_worker", "force": True},
            True,
        ),
        ("wait_for_boundary", {}, True),
        ("recycle_giw", {"force": True}, True),
        ("recycle_giw", {}, True),
        ("rebuild", {}, True),
        ("stop", {}, True),
        ("charter_reload", {}, True),
        ("busy_status", {"service": "mcp"}, False),
    ],
)
def test_ac7_allowlist_refusals(
    method: str, params: dict[str, Any], expect_refused: bool
) -> None:
    reason = refuse_manage_call(method, params)
    if expect_refused:
        assert reason is not None, f"expected refusal for {method} {params}"
    else:
        assert reason is None, f"unexpected refusal: {reason}"


@pytest.mark.offline
def test_falsifier_map_row_empty_cite_rejected() -> None:
    data = yaml.safe_load((REPO / "config/maestro/serving-map.yaml").read_text())
    for row in data["rows"]:
        assert str(row.get("cite", "")).strip()


@pytest.mark.offline
def test_falsifier_stale_runbook_sha_in_fixture_refuses(tmp_path: Path) -> None:
    serving_map = load_serving_map()
    root = tmp_path / "files"
    for spec in serving_map.source_pins.values():
        rel = str(spec["uri"]).removeprefix("cortex://")
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("drifted content\n", encoding="utf-8")
    manage = FakeManage()
    result = compute_dry_run(
        land_sha=LAND_4ACC8E10,
        touched_paths=["libs/agent_bus_store/a.py"],
        porcelain_raw="",
        serving_map=serving_map,
        manage=manage,
        files_root=root,
    )
    assert result.refuse_reason == "source_pin_sha_drift"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_handler_pipeline_option_source_pins_ok_does_not_bypass_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source_pins_ok in pipeline_options must not skip verify_source_pins (AC6)."""
    from types import SimpleNamespace

    from pipelines.maestro_landed_live.v1.handlers.dry_run import (
        MaestroLandedLiveDryRunHandler,
    )

    monkeypatch.setattr(
        "maestro_landed.restart_plan.verify_source_pins",
        lambda _pins, *, files_root=None: (False, {"pin": "drift"}),
    )
    handler = MaestroLandedLiveDryRunHandler()
    context = SimpleNamespace(
        options={
            "mode": "dry_run",
            "land_sha": LAND_4ACC8E10,
            "touched_paths": ["libs/agent_bus_store/a.py"],
            "source_pins_ok": True,
        }
    )
    out = await handler.execute(step=None, context=context)
    assert out.json is not None
    assert out.json.get("status") == "REFUSE"
    assert out.json.get("refuse_reason") == "source_pin_sha_drift"


@pytest.mark.offline
def test_yaml_step_type_matches_handlers() -> None:
    import importlib.util
    import sys

    yaml_path = REPO / "pipelines/maestro_landed_live/v1/maestro-landed-live-v1.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    yaml_types = {step["type"] for step in data["steps"]}
    init = yaml_path.parent / "handlers" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "_pipeline_handlers_maestro_landed_live_v1",
        init,
        submodule_search_locations=[str(init.parent)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    recorded: list[str] = []

    class Rec:
        def register_domain_handler_class(self, domain, step_type, handler_class, *, external=False):
            recorded.append(step_type)

    mod.register_handlers(Rec())
    assert set(recorded) == yaml_types
