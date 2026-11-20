"""Shared fixtures for houseless seat parked-row tests (Packet 1)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles import cdp_registry_store as store_mod
from claude_bundles import cse_provenance
from claude_bundles.cdp_registry.execution_state import set_execution_state
from claude_bundles.cdp_registry.models import STATUS_TAB

CHAT_U = "https://claude.ai/chat/2762063d-2966-4859-a3ce-23831c6e0c86"
CHAT_OTHER = "https://claude.ai/chat/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MARKER = "#4-unique: houseless-seat-wake-2026"
WAKE_PROMPT = f"TYPE: BREAK_IN\n{MARKER}\nwake\n"


def _paste_eval_sequence(url: str | list[str] | None = None) -> list[Any]:
    after = {
        "count": 2,
        "last_len": 80,
        "last_snippet": WAKE_PROMPT[:400],
        "last_text": WAKE_PROMPT,
    }
    return [
        {"count": 1, "last_len": 10, "last_snippet": "old", "last_text": "old"},
        after,
        True,
        True,
        after,
        True,
    ]


def _make_playwright_page(
    target_id: str,
    url: str | list[str],
    *,
    evaluate_results: list[Any] | None = None,
) -> Any:
    from unittest.mock import AsyncMock

    page = AsyncMock()
    page.target_id = target_id
    urls = list(url) if isinstance(url, list) else [url]
    idx = {"i": 0}

    def _read_url() -> str:
        if idx["i"] < len(urls) - 1:
            u = urls[idx["i"]]
            idx["i"] += 1
            return u
        return urls[-1]

    type(page).url = property(lambda self: _read_url())
    from itertools import chain, repeat

    seq = list(evaluate_results or _paste_eval_sequence(url))
    after = seq[1] if len(seq) > 1 else {"count": 2, "last_text": WAKE_PROMPT}
    page.evaluate = AsyncMock(side_effect=chain(seq, repeat(after)))
    loc = AsyncMock()
    loc.count = AsyncMock(return_value=0)
    page.locator = lambda *_a, **_k: loc
    return page


class SeatPage:
    """Alias retained for tests that assign ``url`` lists on pages_by_tid."""

    def __init__(self, target_id: str, url: str | list[str]) -> None:
        self._inner = _make_playwright_page(target_id, url)
        self.target_id = target_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Pw:
    async def stop(self) -> None:
        return None


@pytest.fixture
def seat_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    paths = {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "SESSIONS_JSON": root / "sessions.json",
        "SESSION_TRANSITIONS_JSONL": root / "session_transitions.jsonl",
        "PORTS_LOCK": root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }
    for name, value in paths.items():
        monkeypatch.setattr(reg._store, name, value)
        monkeypatch.setattr(store_mod, name, value)
        monkeypatch.setattr(cse_provenance.store, name, value)
    for name in (
        "REGISTRY_DIR",
        "REGISTRY_LOG",
        "ACTIVE_JSON",
        "PORTS_LOCK",
        "REGISTRATIONS_DIR",
    ):
        monkeypatch.setattr(reg, name, paths[name])
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    live: set[int] = {9224, 9225, 9226}
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda port: port in live)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.tab_rows._pin_port_block",
        lambda: set(),
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_lane.cdp_display_candidates",
        lambda account_id=None: [":2"],
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.tab_rows._TAB_MINT_LOCK",
        tmp_path / "tab_host_mint.lock",
    )
    monkeypatch.setattr(
        "claude_bundles.cse_provenance.append_episode", lambda **_k: None
    )
    kills: list[int] = []
    monkeypatch.setattr(
        reg, "_kill_listener", lambda port: kills.append(port), raising=False
    )
    monkeypatch.setattr(reg, "RECLAIM_TRASH_DIR", tmp_path / "trash", raising=False)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    cortex_root = tmp_path / "cortex"
    cortex_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("ULG_REPO", str(tmp_path / "empty-repo"))
    monkeypatch.setenv("CDP_SEAT_ACCOUNTS_PATH", str(tmp_path / "no-accounts.yaml"))
    opened: dict[str, str] = {}
    ops: list[tuple[Any, ...]] = []
    open_lock = threading.Lock()
    counter = {"n": 0}
    pages_by_tid: dict[str, Any] = {}

    def fake_open(port: int, url: str) -> str:
        with open_lock:
            tid = f"t{counter['n']}"
            counter["n"] += 1
            opened[tid] = url
            page = _make_playwright_page(tid, url)
            pages_by_tid[tid] = page
            ops.append(("open", port, tid, url))
        return tid

    def fake_close(port: int, tid: str) -> None:
        ops.append(("close", port, tid))
        opened.pop(tid, None)
        pages_by_tid.pop(tid, None)

    def _refresh_paste_eval(page: Any) -> None:
        from itertools import chain, repeat
        from unittest.mock import AsyncMock

        seq = list(_paste_eval_sequence(CHAT_U))
        after = seq[1]
        ev = AsyncMock(side_effect=chain(seq, repeat(after)))
        page.evaluate = ev

    async def fake_connect(_cdp_url: str) -> tuple[Any, Any, Any, Any]:
        ctx = type("Ctx", (), {})()
        pages = list(pages_by_tid.values())
        if not pages:
            pages = [_make_playwright_page("decoy-first", "about:blank")]
        for page in pages:
            _refresh_paste_eval(page)
        ctx.pages = pages
        return _Pw(), None, ctx, None

    monkeypatch.setattr("claude_bundles.cdp_lane.open_cdp_tab_target", fake_open)
    monkeypatch.setattr("claude_bundles.cdp_lane.close_cdp_target", fake_close)
    monkeypatch.setattr("claude_bundles.skills_ui_panel.connect_cdp", fake_connect)
    monkeypatch.setattr(
        "browser_governance.census.check_ephemeral_admission_preflight",
        lambda **_k: None,
    )

    async def _label(_page: Any) -> str:
        return "stub-model"

    monkeypatch.setattr("claude_bundles.chat_model_select.current_model_label", _label)
    monkeypatch.setattr("cdp_ask.execution_resume.current_model_label", _label)
    return {
        "live": live,
        "ops": ops,
        "kills": kills,
        "root": root,
        "pages_by_tid": pages_by_tid,
        "profiles": profiles,
    }


@contextmanager
def paste_env(*, holds: bool = False, survives: bool = True) -> Iterator[AsyncMock]:
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ) as send_mock,
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new_callable=AsyncMock,
            return_value={
                "streaming": False,
                "url": CHAT_U,
            },
        ),
        patch(
            "claude_bundles.project_ask_conversation.composer_holds_draft",
            new_callable=AsyncMock,
            return_value=holds,
        ),
        patch(
            "claude_bundles.project_ask_conversation.marker_survives_settle",
            new_callable=AsyncMock,
            return_value=survives,
        ),
    ):
        yield send_mock


def host_row(
    host_id: str,
    port: int,
    *,
    chat_url: str | None = None,
    status: str = "active",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "registration_id": host_id,
        "status": status,
        "port": port,
        "display": ":2",
        "profile_suffix": f"p-{host_id}",
        "holder": "cdp-tab-pool",
        "purpose": "tab_host",
        "account_id": "default",
        "holder_pid": os.getpid(),
        "started_at": time.time(),
    }
    if chat_url:
        row["chat_url"] = chat_url
    return row


def seat_row(
    rid: str,
    host_id: str,
    port: int,
    chat_url: str,
    *,
    target_id: str | None = None,
    kept: str | None = "houseless_seat",
    holder_pid: int | None = None,
    purpose: str = "houseless",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "registration_id": rid,
        "status": STATUS_TAB,
        "host_registration_id": host_id,
        "port": port,
        "holder": "ask-holder",
        "purpose": purpose,
        "profile_suffix": "p-tab",
        "display": ":2",
        "target_id": target_id,
        "chat_url": chat_url,
        "holder_pid": os.getpid() if holder_pid is None else holder_pid,
        "started_at": time.time(),
    }
    if kept:
        row["tab_kept_reason"] = kept
    return row


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def stamp(
    rid: str,
    execution_id: str,
    state: str,
    *,
    kind: str = "execution",
) -> None:
    set_execution_state(
        rid,
        execution_id=execution_id,
        state=state,
        reason="test-houseless",
        kind=kind,
    )


def journal_tab_kept_count(root: Path, rid: str) -> int:
    log = root / "registry.jsonl"
    if not log.is_file():
        return 0
    n = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        import json

        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "tab_kept" and obj.get("registration_id") == rid:
            n += 1
    return n
