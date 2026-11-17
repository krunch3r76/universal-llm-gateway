"""Per-dispatch Cowork Project selection on CDP generate.

Follows ``project_uuid`` / ``project_binding`` / ``claude_project_name`` from
the team_dispatch body through admit, the worker, and the satellite submit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cdp_ask.claude_project_defaults import DEFAULT_CLAUDE_PROJECT_NAME
from fastapi import Response

from systems.frontier_consult import cdp_generate as mod
from systems.frontier_consult import cdp_project_bindings as bindings
from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cdp_project_bindings import (
    CdpDispatchProject,
    resolve_cdp_dispatch_project,
)
from systems.frontier_consult.route import TeamDispatchGenerateBody

_IMPRESARIO_UUID = "0198c0de-0000-7000-8000-00000000cafe"
_EXPLICIT_UUID = "0198c0de-1111-7000-8000-00000000beef"
_ROUTE_UUID = "0198c0de-2222-7000-8000-00000000f00d"


@pytest.fixture(autouse=True)
def _hermetic_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only ``impresario`` is configured; every other binding key is absent."""
    monkeypatch.setattr(
        bindings, "_load_bindings", lambda: {"impresario": _IMPRESARIO_UUID}
    )


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        pytest.param({}, CdpDispatchProject(), id="omitted-keeps-default"),
        pytest.param(
            {"claude_project_name": "staging"},
            CdpDispatchProject(claude_project_name="staging"),
            id="name-only",
        ),
        pytest.param(
            {"project_binding": "impresario", "claude_project_name": "staging"},
            CdpDispatchProject(project_uuid=_IMPRESARIO_UUID),
            id="binding-outranks-name",
        ),
        pytest.param(
            {
                "project_uuid": _EXPLICIT_UUID,
                "project_binding": "absent",
                "claude_project_name": "staging",
            },
            CdpDispatchProject(project_uuid=_EXPLICIT_UUID),
            id="uuid-outranks-unconfigured-binding",
        ),
        pytest.param(
            {"server_project_uuid": _ROUTE_UUID, "project_uuid": _EXPLICIT_UUID},
            CdpDispatchProject(project_uuid=_ROUTE_UUID),
            id="route-bound-uuid-outranks-caller",
        ),
        pytest.param(
            {
                "project_uuid": "  ",
                "project_binding": "",
                "claude_project_name": " staging ",
            },
            CdpDispatchProject(claude_project_name="staging"),
            id="blank-counts-as-omitted",
        ),
    ],
)
def test_resolver_precedence(
    fields: dict[str, str], expected: CdpDispatchProject
) -> None:
    """Breaks when a lower field wins, or a lower field is read after a hit."""
    resolved = resolve_cdp_dispatch_project(
        request_id="req-precedence", claude_surface=None, **fields
    )
    assert resolved == expected


def test_resolver_code_surface_ignores_caller_fields() -> None:
    """Breaks when claude.ai/code admits refuse or forward a Cowork Project."""
    resolved = resolve_cdp_dispatch_project(
        request_id="req-code",
        claude_surface="code",
        project_uuid="not-a-uuid",
        project_binding="absent",
        claude_project_name="staging",
    )
    assert resolved == CdpDispatchProject()


def test_resolver_normalizes_then_rejects_project_uuid() -> None:
    """Breaks when a display name pasted into ``project_uuid`` reaches compose."""
    resolved = resolve_cdp_dispatch_project(
        request_id="req-uuid",
        claude_surface="cowork",
        project_uuid=_EXPLICIT_UUID.upper(),
    )
    assert resolved.project_uuid == _EXPLICIT_UUID

    with pytest.raises(FrontierEndpointError) as excinfo:
        resolve_cdp_dispatch_project(
            request_id="req-uuid-bad",
            claude_surface="cowork",
            project_uuid="Grand Impresario",
        )
    err = excinfo.value
    assert (err.status_code, err.code, err.field) == (
        422,
        "cdp_project_uuid_invalid",
        "project_uuid",
    )


def _generate_body(**project_fields: Any) -> TeamDispatchGenerateBody:
    return TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="24076",
        prompt="project bind probe\n",
        caller_agent="cursor-sdk-test",
        **project_fields,
    )


async def _admit_worker_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **project_fields: Any
) -> dict[str, Any]:
    """Admit one hermetic generate; return the kwargs handed to the worker."""
    from systems.frontier_consult.test_cdp_generate import _stub_cdp_generate_after_gate

    _stub_cdp_generate_after_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.record_spawn",
        lambda **_k: {"ok": True},
    )
    captured: list[dict[str, Any]] = []
    pending: list[Any] = []

    async def _worker(**kwargs: Any) -> None:
        captured.append(dict(kwargs))

    class _FakeTask:
        def add_done_callback(self, _cb: object) -> None:
            return None

    def _capture_task(coro: Any, **_kwargs: object) -> _FakeTask:
        pending.append(coro)
        return _FakeTask()

    monkeypatch.setattr(mod, "run_cdp_worker", _worker)
    monkeypatch.setattr(mod.asyncio, "create_task", _capture_task)

    await mod.dispatch_cdp_generate(
        request_id="req-project-bind",
        body=_generate_body(**project_fields),
        response=Response(),
    )
    assert pending
    await pending[0]
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
async def test_admit_missing_binding_refuses_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured ``project_binding`` is 422 with no spawn-ledger row.

    Breaks when the key falls through to the default Project, or when the
    refusal fires after the admit already wrote its ledger row.
    """
    spawned: list[dict[str, Any]] = []

    def _record_spawn(**kwargs: Any) -> dict[str, Any]:
        spawned.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.record_spawn", _record_spawn
    )
    with pytest.raises(FrontierEndpointError) as excinfo:
        await mod.dispatch_cdp_generate(
            request_id="req-binding-missing",
            body=_generate_body(project_binding="absent"),
            response=Response(),
        )
    err = excinfo.value
    assert (err.status_code, err.code, err.field) == (
        422,
        "cdp_project_binding_missing",
        "project_binding",
    )
    assert spawned == []


@pytest.mark.asyncio
async def test_admit_forwards_claude_project_name_to_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when the body's Project name is dropped before ``run_cdp_worker``."""
    kwargs = await _admit_worker_kwargs(
        monkeypatch, tmp_path, claude_project_name="staging"
    )
    assert kwargs["claude_project_name"] == "staging"
    assert "project_uuid" not in kwargs


@pytest.mark.asyncio
async def test_admit_project_uuid_outranks_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when a name rides along with an explicit uuid and is resolved."""
    kwargs = await _admit_worker_kwargs(
        monkeypatch,
        tmp_path,
        project_uuid=_EXPLICIT_UUID,
        claude_project_name="staging",
    )
    assert kwargs["project_uuid"] == _EXPLICIT_UUID
    assert "claude_project_name" not in kwargs


@pytest.mark.asyncio
async def test_admit_resolves_project_binding_to_uuid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when the binding key, not its uuid, reaches the worker."""
    kwargs = await _admit_worker_kwargs(
        monkeypatch, tmp_path, project_binding="impresario"
    )
    assert kwargs["project_uuid"] == _IMPRESARIO_UUID
    assert "claude_project_name" not in kwargs


@pytest.mark.asyncio
async def test_admit_code_surface_ignores_project_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when a Project field refuses or leaks into a claude.ai/code admit."""
    monkeypatch.setattr(
        "implement_admission.claude_code_dispatch.load_claude_code_dispatch_mode",
        lambda *_a, **_k: "allow",
    )
    kwargs = await _admit_worker_kwargs(
        monkeypatch,
        tmp_path,
        claude_surface="code",
        claude_project_name="staging",
        project_binding="absent",
    )
    assert kwargs["claude_surface"] == "code"
    assert "claude_project_name" not in kwargs
    assert "project_uuid" not in kwargs


@pytest.mark.asyncio
async def test_admit_without_project_fields_sends_no_project_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when an admit that names no Project stops using the default."""
    kwargs = await _admit_worker_kwargs(monkeypatch, tmp_path)
    assert "claude_project_name" not in kwargs
    assert "project_uuid" not in kwargs


async def _submit_via_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **worker_fields: Any
) -> Any:
    """Run the worker against a fake satellite; return its one submit request."""
    from claude_bundles.cdp_model_endpoint_staging import stage_cdp_prompt_with_skills

    from systems.frontier_consult.cdp_generate_worker import run_cdp_worker
    from systems.frontier_consult.test_cdp_omitted_purpose import (
        _FakeAsk,
        _patch_worker_io,
    )

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    from agent_bus_store.db import init_db

    init_db()
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-project-bind",
        prompt_text="project bind body\n",
        purpose=None,
    )
    ask = _FakeAsk()
    _patch_worker_io(monkeypatch, ask)
    await run_cdp_worker(
        execution_id="exec-project-bind",
        model_id="cdp/opus-5.5",
        thread_id="24076",
        caller_agent="cursor",
        prompt_uri=staged.prompt_uri,
        request_id="req-project-bind",
        **worker_fields,
    )
    assert len(ask.submitted) == 1
    return ask.submitted[0]


@pytest.mark.asyncio
async def test_worker_submits_claude_project_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when the worker or adapter drops the name before the submit."""
    from cdp_ask.project_select import project_select_needed

    req = await _submit_via_worker(
        monkeypatch, tmp_path, claude_project_name="staging"
    )
    assert req.claude_project_name == "staging"
    assert req.project_uuid == ""
    assert project_select_needed(req) is True


@pytest.mark.asyncio
async def test_worker_project_uuid_skips_name_resolve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when an explicit uuid still sends the satellite to resolve a name."""
    from cdp_ask.project_select import project_select_needed

    req = await _submit_via_worker(monkeypatch, tmp_path, project_uuid=_EXPLICIT_UUID)
    assert req.project_uuid == _EXPLICIT_UUID
    assert req.no_project_uuid is False
    assert project_select_needed(req) is False


@pytest.mark.asyncio
async def test_worker_default_submit_keeps_default_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when a dispatch that names no Project stops resolving the default."""
    req = await _submit_via_worker(monkeypatch, tmp_path)
    assert req.claude_project_name == DEFAULT_CLAUDE_PROJECT_NAME
    assert req.project_uuid == ""
    assert req.no_project_uuid is True
