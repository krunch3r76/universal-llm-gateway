"""op=spawn wire validation on the Stargate team_dispatch route (21088#104 R1)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


def _client() -> TestClient:
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "services" / "universal-stargate"))
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError

    from systems.frontier_consult.route import team_router
    from systems.proxy.app import validation_exception_handler

    surface = FastAPI()
    surface.include_router(team_router)
    surface.add_exception_handler(RequestValidationError, validation_exception_handler)
    return TestClient(surface)


def _spawn_body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "op": "spawn",
        "backend": "cursor_ide",
        "gui_host": ":8",
        "thread": "thread-house-1",
        "spawn_purpose": "one line purpose",
        "prompt": "Do the work.",
    }
    base.update(overrides)
    return base


def _post(client: TestClient, body: dict[str, object]):
    proxy = MagicMock(event_bus=None)
    with patch("systems.proxy.dependencies.get_proxy", return_value=proxy):
        return client.post("/api/v1/team/dispatch", json=body)


def _assert_422(response, *, code: str, field: str) -> None:
    assert response.status_code == 422, response.text
    payload = response.json()
    assert payload["error"]["code"] == code, payload
    assert payload.get("field") == field, payload


def test_op_spawn_requires_spawn_purpose() -> None:
    client = _client()
    for bad in ({"spawn_purpose": None}, {"spawn_purpose": ""}, {"spawn_purpose": "a\nb"}):
        resp = _post(client, _spawn_body(**bad))
        _assert_422(resp, code="spawn_purpose_required", field="spawn_purpose")


def test_op_spawn_requires_thread() -> None:
    client = _client()
    body = _spawn_body()
    del body["thread"]
    resp = _post(client, body)
    _assert_422(resp, code="spawn_thread_required", field="thread")


def test_op_spawn_rejects_non_ide_backend() -> None:
    client = _client()
    resp = _post(client, _spawn_body(backend="cursor_sdk"))
    _assert_422(resp, code="spawn_backend_unsupported", field="backend")


def test_op_spawn_cursor_ide_prepends_prefix_before_relay() -> None:
    """R4: route prepends ledger_row + purpose lines to prompt before relay_spawn_tab."""
    client = _client()
    captured: dict[str, str] = {}

    def _relay(**kwargs: object) -> dict[str, object]:
        captured["prompt"] = str(kwargs.get("prompt") or "")
        return {"ok": True, "agent_id": "agent-test"}

    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.bridge_spawn_relay.relay_spawn_tab",
            side_effect=_relay,
        ),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())

    assert resp.status_code == 200, resp.text
    prompt = captured.get("prompt") or ""
    assert prompt.startswith("ledger_row: ")
    assert "\npurpose: one line purpose\n" in prompt
    assert prompt.endswith("Do the work.")


def test_ide_launch_error_projects_failed_status_row(tmp_path: Path) -> None:
    """B3: relay failure writes kind=status failed visible to project_row_status."""
    client = _client()

    def _relay_fail(**kwargs: object) -> dict[str, object]:
        return {"ok": False, "error": "no_page"}

    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.bridge_spawn_relay.relay_spawn_tab",
            side_effect=_relay_fail,
        ),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=tmp_path / "cursor-bridge-tab-wake-ledger.jsonl",
        ),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())
    assert resp.status_code == 502
    payload = resp.json()
    row_id = str(payload.get("ledger_row_id") or "")
    from cursor_bridge.spawn_endpoint_house_ledger import project_row_status

    proj = project_row_status(row_id, watch_dir=tmp_path)
    assert proj["status"] == "failed"
    assert proj["source_status"] == "no_page"


def test_ide_launch_error_marks_failed_after_spine(tmp_path: Path) -> None:
    client = _client()

    def _relay_fail(**kwargs: object) -> dict[str, object]:
        return {"ok": False, "error": "no_page"}

    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.bridge_spawn_relay.relay_spawn_tab",
            side_effect=_relay_fail,
        ),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=tmp_path / "cursor-bridge-tab-wake-ledger.jsonl",
        ),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())
    assert resp.status_code == 502
    from cursor_bridge.spawn_endpoint_house_ledger import (
        _fold_spawn_rows,
        ledger_path,
        read_all_rows,
    )

    rows = read_all_rows(ledger_path(tmp_path))
    spawns = list(_fold_spawn_rows(rows).values())
    assert len(spawns) == 1
    assert spawns[0]["state"] == "failed"


def test_ide_launch_relay_exception_marks_failed_not_500(tmp_path: Path) -> None:
    client = _client()

    def _relay_boom(**kwargs: object) -> dict[str, object]:
        raise RuntimeError("relay exploded")

    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.bridge_spawn_relay.relay_spawn_tab",
            side_effect=_relay_boom,
        ),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=tmp_path / "cursor-bridge-tab-wake-ledger.jsonl",
        ),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())
    assert resp.status_code == 502, resp.text
    spawns = _folded_spawns(tmp_path / "cursor-bridge-tab-wake-ledger.jsonl")
    assert len(spawns) == 1
    assert spawns[0]["state"] == "failed"


def test_op_ledger_house_object_disambiguates(tmp_path: Path) -> None:
    from cursor_bridge.spawn_endpoint_house_ledger import record_spawn

    record_spawn(
        surface="cursor_ide",
        thread="house-thread",
        purpose="first",
        purpose_unstated=False,
        dispatch_id="disp-one",
        watch_dir=tmp_path,
    )
    record_spawn(
        surface="cursor_ide",
        thread="house-thread",
        purpose="second",
        purpose_unstated=False,
        dispatch_id="disp-two",
        watch_dir=tmp_path,
    )
    client = _client()
    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=tmp_path / "cursor-bridge-tab-wake-ledger.jsonl",
        ),
    ):
        resp = client.post(
            "/api/v1/team/dispatch",
            json={
                "op": "ledger",
                "house": {"thread": "house-thread", "dispatch_id": "disp-two"},
                "limit": 5,
            },
        )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["dispatch_id"] == "disp-two"


def test_ide_spawn_row_launching_before_relay(tmp_path: Path) -> None:
    client = _client()
    order: list[str] = []

    def _relay(**kwargs: object) -> dict[str, object]:
        order.append("relay")
        return {"ok": True, "editor_agent_id": "agent-99"}

    def _record_spawn(**kwargs: object) -> dict[str, object]:
        order.append("spawn")
        return {"ok": True, "row_id": "a" * 32}

    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.bridge_spawn_relay.relay_spawn_tab",
            side_effect=_relay,
        ),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.record_spawn",
            side_effect=_record_spawn,
        ),
        patch("cursor_bridge.spawn_endpoint_house_ledger.patch_spawn_row"),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())
    assert resp.status_code == 200
    assert order == ["spawn", "relay"]
    assert order.index("spawn") < order.index("relay")


def test_op_ledger_house_trims_to_5120(tmp_path: Path) -> None:
    from cursor_bridge.spawn_endpoint_house_ledger import record_spawn

    blob = "x" * 480
    for i in range(12):
        record_spawn(
            surface="cursor_ide",
            thread="house-thread",
            purpose=f"{blob}-{i}",
            purpose_unstated=False,
            watch_dir=tmp_path,
        )
    client = _client()
    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=tmp_path / "cursor-bridge-tab-wake-ledger.jsonl",
        ),
    ):
        resp = client.post(
            "/api/v1/team/dispatch",
            json={"op": "ledger", "house": "house-thread", "limit": 10},
        )
    assert resp.status_code == 200
    assert len(resp.content) <= 5120
    body = resp.json()
    assert body["budget"]["max_bytes"] == 5120
    assert body["budget"]["actual_bytes"] <= 5120
    assert body["budget"]["truncated"] is True


def _rows(path: Path) -> list[dict]:
    import json

    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _folded_spawns(path: Path) -> list[dict]:
    from cursor_bridge.spawn_endpoint_house_ledger import _fold_spawn_rows, read_all_rows

    return list(_fold_spawn_rows(read_all_rows(path)).values())


def test_glass_first_message_prefix(tmp_path: Path) -> None:
    """Break: IDE first message omits the minted row_id or the persisted spawn_purpose."""
    client = _client()
    captured: dict[str, str] = {}

    def _relay(**kwargs: object) -> dict[str, object]:
        captured["prompt"] = str(kwargs.get("prompt") or "")
        return {"ok": True, "agent_id": "agent-glass"}

    proxy = MagicMock(event_bus=None)
    ledger = tmp_path / "cursor-bridge-tab-wake-ledger.jsonl"
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch("cursor_bridge.bridge_spawn_relay.relay_spawn_tab", side_effect=_relay),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=ledger,
        ),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())
    assert resp.status_code == 200, resp.text
    spawns = _folded_spawns(ledger)
    assert len(spawns) == 1
    row_id = spawns[0]["row_id"]
    prompt = captured["prompt"]
    assert prompt.startswith(f"ledger_row: {row_id}\npurpose: one line purpose\n")


def test_ide_spawn_row_agent_id_from_spawn_tab(tmp_path: Path) -> None:
    """Break: agent_id is taken from the request instead of relay_spawn_tab."""
    client = _client()

    def _relay(**kwargs: object) -> dict[str, object]:
        return {"ok": True, "agent_id": "from-relay"}

    proxy = MagicMock(event_bus=None)
    ledger = tmp_path / "cursor-bridge-tab-wake-ledger.jsonl"
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch("cursor_bridge.bridge_spawn_relay.relay_spawn_tab", side_effect=_relay),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=ledger,
        ),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())
    assert resp.status_code == 200, resp.text
    spawns = _folded_spawns(ledger)
    assert len(spawns) == 1
    assert spawns[0]["state"] == "live"
    assert spawns[0]["agent_id"] == "from-relay"
    assert spawns[0]["thread"] == "thread-house-1"
    assert spawns[0]["gui_host"] == ":8"


def test_spawn_purpose_not_cdp_purpose_tag(tmp_path: Path) -> None:
    """Break: spawn_purpose is copied into a CDP purpose tag on the spawn response."""
    client = _client()
    proxy = MagicMock(event_bus=None)
    ledger = tmp_path / "cursor-bridge-tab-wake-ledger.jsonl"
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.bridge_spawn_relay.relay_spawn_tab",
            return_value={"ok": True, "agent_id": "a1"},
        ),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=ledger,
        ),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("purpose") != "one line purpose"
    spawns = _folded_spawns(ledger)
    assert spawns[0]["purpose"] == "one line purpose"
    assert spawns[0]["purpose_unstated"] is False


def test_op_ledger_work_key_unchanged() -> None:
    client = _client()
    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "systems.frontier_consult.cursor_sdk_claim_dispatch.relay_house_claim",
            new_callable=AsyncMock,
        ) as relay,
    ):
        relay.return_value = (True, {"status_code": 200, "rows": []})
        resp = client.post(
            "/api/v1/team/dispatch",
            json={"op": "ledger", "work_key": "todo:foo", "limit": 5},
        )
    relay.assert_called_once()
    assert resp.status_code == 200


def test_generate_does_not_require_spawn_purpose() -> None:
    """Break: op=generate validation requires TeamDispatchSpawnBody.spawn_purpose."""
    from pydantic import ValidationError

    from systems.frontier_consult.route_models import (
        TeamDispatchGenerateBody,
        TeamDispatchSpawnBody,
    )

    assert "spawn_purpose" not in TeamDispatchGenerateBody.model_fields
    assert "spawn_purpose" in TeamDispatchSpawnBody.model_fields
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        job="freeform",
        model="cursor/grok-4.7",
        lane="B",
        prompt="Do the work.",
    )
    assert not hasattr(body, "spawn_purpose")
    with pytest.raises(ValidationError):
        TeamDispatchGenerateBody(
            op="generate",
            seat="cursor-sdk",
            job="freeform",
            lane="B",
            prompt="Do the work.",
            spawn_purpose="must not bind",
        )


async def _admit_sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **body_kw: object):
    from types import SimpleNamespace

    from starlette.responses import Response

    from systems.frontier_consult.generate_wrap import dispatch_cursor_sdk_generate_route
    from systems.frontier_consult.route_models import TeamDispatchGenerateBody

    async def _prompt(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(text="findings", prompt_turn_number=None, prompt_bind_mode=None)

    async def _threads(**kwargs: object) -> tuple[None, None, bool]:
        return None, None, False

    async def _generate(**kwargs: object) -> dict[str, str]:
        return {"dispatch_id": "disp-late", "execution_id": "exec-1", "status": "running"}

    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_generate_prompt_resolution",
        _prompt,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.resolve_cursor_sdk_thread_targets",
        _threads,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        _generate,
    )
    ledger = tmp_path / "cursor-bridge-tab-wake-ledger.jsonl"
    monkeypatch.setattr(
        "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
        lambda watch_dir=None: ledger,
    )
    fields: dict[str, object] = {
        "op": "generate",
        "seat": "cursor-sdk",
        "job": "freeform",
        "model": "cursor/grok-4.7",
        "lane": "B",
        "prompt": "Do the work.",
        "dispatch_thread_id": "thread-sdk",
    }
    fields.update(body_kw)
    body = TeamDispatchGenerateBody(**fields)  # type: ignore[arg-type]
    response = Response()
    result = await dispatch_cursor_sdk_generate_route(
        request_id="req-sdk-spine",
        body=body,
        seat="cursor-sdk",
        response=response,
    )
    return result, response, ledger


@pytest.mark.asyncio
async def test_generate_wrap_admit_appends_sdk_spine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: sdk admit appends zero or more than one kind=spawn row."""
    result, response, ledger = await _admit_sdk(tmp_path, monkeypatch)
    assert response.status_code == 202
    assert result["dispatch_id"] == "disp-late"
    spawns = _folded_spawns(ledger)
    assert len(spawns) == 1
    assert spawns[0]["surface"] == "cursor_sdk"


@pytest.mark.asyncio
async def test_generate_unstated_purpose_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: op=generate copies a purpose field onto the sdk spine."""
    _, _, ledger = await _admit_sdk(tmp_path, monkeypatch)
    spawns = _folded_spawns(ledger)
    assert len(spawns) == 1
    assert spawns[0]["purpose"] == "(unstated)"
    assert spawns[0]["purpose_unstated"] is True


@pytest.mark.asyncio
async def test_generate_wrap_late_dispatch_id_patches_one_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: late dispatch_id appends a second spawn row."""
    _, _, ledger = await _admit_sdk(tmp_path, monkeypatch)
    spawns = _folded_spawns(ledger)
    assert len(spawns) == 1
    assert spawns[0]["dispatch_id"] == "disp-late"
    assert spawns[0]["row_id"]


@pytest.mark.asyncio
async def test_generate_wrap_pre_admit_failure_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: a lane-gate failure still appends a spine."""
    from starlette.responses import Response

    from systems.frontier_consult.generate_wrap import dispatch_cursor_sdk_generate_route
    from systems.frontier_consult.route_models import TeamDispatchGenerateBody

    ledger = tmp_path / "cursor-bridge-tab-wake-ledger.jsonl"
    monkeypatch.setattr(
        "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
        lambda watch_dir=None: ledger,
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        job="freeform",
        model="cursor/grok-4.7",
        prompt="Do the work.",
    )
    result = await dispatch_cursor_sdk_generate_route(
        request_id="req-pre",
        body=body,
        seat="cursor-sdk",
        response=Response(),
    )
    assert getattr(result, "status_code", None) == 422
    assert _rows(ledger) == []


@pytest.mark.asyncio
async def test_cdp_generate_spine_keyed_by_execution_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: CDP spine uses execution_id as row_id or omits surface=cdp."""
    from claude_bundles.cdp_model_endpoint_staging import CdpStagingError
    from starlette.responses import Response

    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route_models import TeamDispatchGenerateBody

    async def _busy(**kwargs: object) -> None:
        return None

    def _stage(**kwargs: object) -> object:
        raise CdpStagingError("stopped after spine", code="cdp_prompt_missing")

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare_restart_busy.enforce_restart_busy_scoped_by_work_key",
        _busy,
    )
    monkeypatch.setattr("systems.frontier_consult.cdp_generate._stage_inputs", _stage)
    ledger = tmp_path / "cursor-bridge-tab-wake-ledger.jsonl"
    monkeypatch.setattr(
        "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
        lambda watch_dir=None: ledger,
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        model="cdp/opus-5",
        prompt="hello",
        dispatch_thread_id="thread-cdp",
    )
    with pytest.raises(FrontierEndpointError):
        await dispatch_cdp_generate(
            request_id="req-cdp",
            body=body,
            response=Response(),
        )
    spawns = _folded_spawns(ledger)
    assert len(spawns) == 1
    row = spawns[0]
    assert row["surface"] == "cdp"
    assert row["execution_id"]
    assert row["execution_id"] != row["row_id"]
    assert len(row["row_id"]) == 32


def test_cdp_stage_inputs_prefixes_ledger_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break: CDP staged_prompt lacks the ledger_row prefix."""
    from types import SimpleNamespace

    from systems.frontier_consult.cdp_generate import _stage_inputs

    captured: dict[str, str] = {}

    def _prep(**kwargs: object) -> tuple:
        return ("hello body", None, None, None)

    class _Stamp:
        stamped = False
        body = "hello body"
        source_label = ""

    def _stamp(**kwargs: object) -> _Stamp:
        return _Stamp()

    def _stage(**kwargs: object) -> SimpleNamespace:
        captured["prompt"] = str(kwargs.get("prompt_text") or "")
        return SimpleNamespace(prompt_uri="cortex://staged")

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate.prepare_cdp_staging_sources", _prep
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate.stamp_cdp_packet_mcp_default", _stamp
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate.stage_cdp_prompt_with_skills", _stage
    )
    row_id = "ab" * 16
    _stage_inputs(
        execution_id="exec-stage",
        prompt="hello body",
        sidecar_ref=None,
        packet_path=None,
        ledger_row_id=row_id,
        ledger_purpose="(unstated)",
    )
    assert captured["prompt"].startswith(f"ledger_row: {row_id}\npurpose: (unstated)\n")
