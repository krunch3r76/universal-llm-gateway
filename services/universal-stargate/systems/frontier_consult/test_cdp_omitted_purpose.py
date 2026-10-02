"""Omitted-purpose CDP generate: worker restage and admit ledger.

Breaks when the worker forwards purpose=\"ask\" into staging (libs notice
and arch skill floor) or when admit stores \"ask\" for an omitted purpose.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_bundles.cdp_model_endpoint_staging import stage_cdp_prompt_with_skills

_NOTICE = "Primitives in libs/ are often the right tool"
_SKILL_MARKER = re.compile(r"<!--cdp-required-skills:([^>]*)-->")
_ARCH = ("architecture-invariants", "ulg-architecture", "hypothesize-simulate")


def _skill_slugs(text: str) -> list[str]:
    match = _SKILL_MARKER.search(text)
    assert match, "sealed prompt missing cdp-required-skills marker"
    return [part for part in match.group(1).split(",") if part]


class _FakeAsk:
    def __init__(self) -> None:
        self.submitted: list[Any] = []
        self.prompt_at_submit: str = ""

    def submit(self, submit_req: Any, **kwargs: Any) -> dict[str, Any]:
        self.submitted.append(submit_req)
        from claude_bundles.cdp_model_endpoint_staging import cortex_files_root

        uri = str(submit_req.prompt_uri)
        rel = uri.removeprefix("cortex://").lstrip("/")
        path = cortex_files_root() / rel
        if path.is_dir():
            path = path / "prompt.md"
        self.prompt_at_submit = path.read_text(encoding="utf-8")
        return {
            "execution_id": "sat-omit",
            "status": "running",
            "completion_phase": "running",
            "body_len": 0,
        }

    def poll(self, execution_id: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "execution_id": execution_id,
            "status": "complete",
            "completion_phase": "content_proof",
            "content_proof_uri": "cortex://notes/system/threads/proof.md",
            "body": "done",
            "body_len": 4,
        }


def _patch_worker_io(monkeypatch: pytest.MonkeyPatch, ask: _FakeAsk) -> None:
    from systems.frontier_consult import cdp_generate_worker as worker_mod

    monkeypatch.setattr(
        "claude_bundles.cdp_model_endpoint.CdpAskClient",
        lambda: ask,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.maybe_expand_cdp_prompt",
        lambda **kw: kw["prompt_uri"],
    )
    monkeypatch.setattr(worker_mod, "publish_cdp_kwargs", lambda *a, **k: None)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.finalize_cdp_generate",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.attach_satellite_execution_id",
        lambda **kw: None,
    )


@pytest.mark.asyncio
async def test_worker_restage_omitted_purpose_omits_libs_notice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Worker re-entry with purpose None must not stamp the libs notice.

    Breaks when run_cdp_worker passes purpose=\"ask\" into
    stage_cdp_prompt_with_skills and _stamp_owned_ephemeral_notice appends
    the marker onto scratch prompt.md.
    """
    from systems.frontier_consult.cdp_generate_worker import run_cdp_worker

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-f1-notice",
        prompt_text="omitted purpose body\n",
        purpose=None,
    )
    on_disk = tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-f1-notice/prompt.md"
    assert _NOTICE not in on_disk.read_text(encoding="utf-8")
    ask = _FakeAsk()
    _patch_worker_io(monkeypatch, ask)
    await run_cdp_worker(
        execution_id="exec-f1-notice",
        model_id="cdp/opus-5.5",
        thread_id="14656",
        caller_agent="cursor",
        prompt_uri=staged.prompt_uri,
        request_id="req-f1-notice",
        purpose=None,
    )
    assert ask.submitted
    assert "omitted purpose body" in ask.prompt_at_submit
    assert _NOTICE not in ask.prompt_at_submit


@pytest.mark.asyncio
async def test_worker_presealed_sidecar_keeps_judgment_skill_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pre-sealed reasoning-posture prompt stays that floor when purpose is omitted.

    Breaks when the worker restages a sealed cortex:// prompt with
    purpose=\"ask\" and ensure_cdp_judgment_skills adds the arch floor.
    """
    from systems.frontier_consult.cdp_generate_worker import run_cdp_worker

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    stage_cdp_prompt_with_skills(
        execution_id="exec-f1b-seed",
        prompt_text="sealed judgment body\n",
        purpose=None,
        skills=["reasoning-posture"],
    )
    seed_path = tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-f1b-seed/prompt.md"
    sealed = tmp_path / "notes/system/threads/presealed-f1b.md"
    sealed.parent.mkdir(parents=True, exist_ok=True)
    sealed.write_text(seed_path.read_text(encoding="utf-8"), encoding="utf-8")
    sidecar = "cortex://notes/system/threads/presealed-f1b.md"
    assert _skill_slugs(sealed.read_text(encoding="utf-8")) == ["reasoning-posture"]
    ask = _FakeAsk()
    _patch_worker_io(monkeypatch, ask)
    await run_cdp_worker(
        execution_id="exec-f1b-worker",
        model_id="cdp/opus-5.5",
        thread_id="14656",
        caller_agent="cursor",
        prompt_uri=sidecar,
        request_id="req-f1b",
        purpose=None,
    )
    assert ask.submitted
    assert ask.submitted[0].prompt_uri == sidecar
    assert _skill_slugs(ask.prompt_at_submit) == ["reasoning-posture"]
    for slug in _ARCH:
        assert slug not in ask.prompt_at_submit


@pytest.mark.asyncio
async def test_admit_omitted_purpose_ledger_stores_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Admit with purpose omitted records None on the inflight leg.

    Breaks when dispatch_cdp_generate stores \"ask\" and the ledger
    misrecords caller intent.
    """
    from systems.frontier_consult import cdp_generate as mod
    from systems.frontier_consult.cdp_generate_inflight_ledger import read_inflight_leg

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(mod, "post_pointer_turn", AsyncMock(return_value=2))
    monkeypatch.setattr(mod, "create_handoff_thread", AsyncMock(return_value="14640"))
    monkeypatch.setattr(
        mod,
        "admit_handoff_dispatch",
        AsyncMock(return_value=MagicMock(reason="ok")),
    )
    monkeypatch.setattr(mod, "emit_poll_hint_from_handoff", lambda **kw: None)
    monkeypatch.setattr(
        mod,
        "build_handoff_result",
        lambda **kw: {
            "handoff_status": "ok",
            "poll_hint": {"thread_id": "14640", "from_agent": "web-anthropic"},
        },
    )
    monkeypatch.setattr(mod, "resolve_poll_wait_seconds", lambda **kw: 5)
    monkeypatch.setattr(mod, "record_cdp_admit", lambda **kw: None)

    captured_worker: list[dict[str, object]] = []
    pending: list[object] = []

    async def _capture_worker(**kwargs: Any) -> None:
        captured_worker.append(dict(kwargs))

    class _FakeTask:
        def add_done_callback(self, _cb: object) -> None:
            return None

        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            return None

    def _capture_task(coro: object, **kwargs: object) -> _FakeTask:
        pending.append(coro)
        return _FakeTask()

    monkeypatch.setattr(mod, "run_cdp_worker", _capture_worker)
    monkeypatch.setattr(mod.asyncio, "create_task", _capture_task)

    body = MagicMock()
    body.op = "generate"
    body.model = "cdp/opus-5.5"
    body.prompt = "omitted purpose admit\n"
    body.sidecar_ref = None
    body.packet_path = None
    body.skills = None
    body.purpose = None
    body.session = None
    body.job = None
    body.seat = None
    body.role = None
    body.dispatch_lane = None
    body.dispatch_thread_id = None
    body.parent_thread = None
    body.mission_kind = None
    body.caller_agent = "cursor"
    body.bus_lifecycle = None
    body.timeout_seconds = None
    body.generation_options = None
    body.predecessor_registration_id = None
    body.contract = None

    response = MagicMock()
    response.status_code = 200
    result = await mod.dispatch_cdp_generate(
        request_id="req-q2-omit",
        body=body,
        response=response,
    )
    assert pending
    await pending[0]
    assert captured_worker
    assert captured_worker[0]["purpose"] is None
    leg = read_inflight_leg(result["execution_id"])
    assert leg is not None
    assert leg.purpose is None
