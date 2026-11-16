"""Acceptance tests for execution resume seat identity verification (AC22)."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cdp_ask.execution_resume import resume_execution
from cdp_ask.execution_store import ExecutionStore

pytestmark = pytest.mark.offline


class _EmptyLocator:
    async def count(self) -> int:
        return 0


class _FakePage:
    """In-process Playwright Page double at the CDP browser boundary."""

    def __init__(self, url: str) -> None:
        self.url = url

    def locator(self, _selector: str) -> _EmptyLocator:
        return _EmptyLocator()

    async def evaluate(self, _script: str, arg: object = None) -> object:
        if isinstance(arg, dict) and "limit" in arg:
            return {
                "title": "Unified Chat - Claude",
                "streaming": False,
                "stop": False,
                "tool_pause": False,
                "spinner": False,
                "aria_busy": False,
                "coverage": "full",
                "truncated": False,
                "turns": [
                    {"author": "user", "text": "Prompt body for resume.", "ordinal": 1},
                    {
                        "author": "assistant",
                        "text": "Substantive resumed assistant reply body.",
                        "ordinal": 2,
                    },
                ],
            }
        return ""


@pytest.mark.asyncio
async def test_resume_refuses_wrong_chat_tab(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attach check refuses mismatched /chat/ uuids and accepts host/case variants."""
    from claude_bundles import cdp_registry_store as store

    uuid_a = "2762063d-2966-4859-a3ce-23831c6e0c86"
    uuid_b = "98be84c4-48a8-4465-ba80-c0cd59fb3b29"
    chat_a = f"https://claude.ai/chat/{uuid_a}"
    chat_b = f"https://claude.ai/chat/{uuid_b}"
    chat_a_variant = f"https://www.claude.ai/chat/{uuid_a.upper()}"

    root = tmp_path / "cdp-registry"
    root.mkdir()
    harvest_root = tmp_path / "harvest"
    (harvest_root / "notes/system/threads").mkdir(parents=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(harvest_root))
    for name, value in {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "PORTS_LOCK": root / "ports.lock",
    }.items():
        monkeypatch.setattr(store, name, value)

    store.write_active(
        {
            "reg-mismatch": {
                "registration_id": "reg-mismatch",
                "port": 9231,
                "status": "dormant",
                "purpose": "ask",
                "chat_url": chat_a,
            },
            "reg-match": {
                "registration_id": "reg-match",
                "port": 9232,
                "status": "dormant",
                "purpose": "operator-proxy",
                "chat_url": chat_a,
            },
        }
    )

    attached_url = chat_b

    async def _fake_ensure_attached(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(
            ok=True,
            page=_FakePage(attached_url),
            pw=None,
            page_only_borrow=False,
            registration_id=None,
            relaunched=True,
            error=None,
        )

    async def _noop_teardown(_outcome: object) -> None:
        return None

    monkeypatch.setattr(
        "cdp_ask.followup_reattach.ensure_cse_attached", _fake_ensure_attached
    )
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_open._teardown_opened", _noop_teardown
    )

    exec_store = ExecutionStore(reaper_interval_s=9999.0)
    rec_bad = await exec_store.adopt(
        execution_id="exec-wrong-tab",
        registration_id="reg-mismatch",
        holder="cdp-ask-satellite",
        purpose="ask",
        parent_thread=None,
        mission_kind=None,
        started_at=time.time(),
        durable_state="streaming",
    )
    await resume_execution(exec_store, rec_bad, trigger="boot")
    settled_bad = await exec_store.get("exec-wrong-tab")
    assert settled_bad is not None
    assert settled_bad.status == "failed"
    assert settled_bad.error == "resume failed: attach_url_mismatch"

    attached_url = chat_a_variant
    rec_ok = await exec_store.adopt(
        execution_id="exec-variant-match",
        registration_id="reg-match",
        holder="cdp-ask-satellite",
        purpose="operator-proxy",
        parent_thread=None,
        mission_kind=None,
        started_at=time.time(),
        durable_state="streaming",
    )
    await resume_execution(exec_store, rec_ok, trigger="boot")
    settled_ok = await exec_store.get("exec-variant-match")
    assert settled_ok is not None
    assert settled_ok.status == "completed"
