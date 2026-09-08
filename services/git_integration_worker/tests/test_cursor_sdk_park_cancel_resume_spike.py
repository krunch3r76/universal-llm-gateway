"""Leg 0a spike — ``CancelRun`` → ``resume_agent`` continuity (steer-restart gate).

The ``park_for_restart`` bind (spec ``cursor-sdk-steer-restart-v1`` § Rivals,
R2) rests on one external fact: after ``run.cancel()`` mid-run the SDK agent
store is still resumable — a second bridge can ``resume_agent(agent_id)`` on
the same store, the continuation turn references the prior work, and the
store's run row is not left ``running``. Force-kill (SIGTERM) leaves the store
``RUNNING`` and blocks ``resume_of`` (friction a:32579); this spike proves the
cooperative cancel does not.

Two variants:

* hermetic — a fake bridge ``Run`` whose ``wait()`` unblocks on ``cancel()``
  pins the contract the GIW park ladder relies on (cancel unblocks ``wait``,
  terminal status ``cancelled``); it cannot speak for the real store.
* live — two real bridges (two processes, the same HOME — the store-A locus
  ``_run_sdk_sync`` reuses on ``resume_of``) against one store root, gated on
  ``CURSOR_API_KEY`` (env or ``~/.gateway/secrets.env``, same feed as
  ``test_cursor_sdk_store_locus_spike``).  Skipped when the key is absent so
  the closeout must quote the skip reason.

Observed 2026-09-08 (composer-2.5, live): cancel after 2 tool calls →
``run.wait()`` status ``cancelled``; store ``index.db`` ``runs`` row
``CANCELLED`` (not RUNNING); ``resume_agent`` + continuation ``finished`` with
first line ``Already created: alpha.txt`` — bind R2 holds.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from services.git_integration_worker.tests.test_cursor_sdk_store_locus_spike import (
    _cursor_api_key_available,
    _inject_cursor_api_key,
)

_TERMINAL_RUN_STATUSES = {"finished", "error", "cancelled", "expired"}
_CANCEL_WAIT_TIMEOUT_S = 240.0
_RESUME_MARKER_FILES = ("alpha", "bravo", "charlie", "delta", "echo")


# ------------------------------------------------------------- hermetic fake


@dataclass
class FakeRunResult:
    status: str
    result: str = ""
    duration_ms: int = 0


@dataclass
class FakeCancellableRun:
    """Bridge ``Run`` stand-in: ``wait()`` blocks until ``cancel()`` or finish."""

    id: str = "run-fake"
    agent_id: str = "agent-fake"
    status: str = "running"
    _done: threading.Event = field(default_factory=threading.Event)
    cancel_calls: int = 0

    def cancel(self) -> None:
        if self.status in _TERMINAL_RUN_STATUSES:
            raise RuntimeError(f"run {self.id} already terminal: {self.status}")
        self.cancel_calls += 1
        self.status = "cancelled"
        self._done.set()

    def finish(self) -> None:
        self.status = "finished"
        self._done.set()

    def wait(self, timeout: float = 5.0) -> FakeRunResult:
        if not self._done.wait(timeout):
            raise TimeoutError("fake run never reached terminal")
        return FakeRunResult(status=self.status)


def test_hermetic_cancel_unblocks_wait_with_cancelled_terminal() -> None:
    """Contract the park ladder relies on: cancel → wait() returns ``cancelled``."""
    run = FakeCancellableRun()
    results: list[FakeRunResult] = []
    worker = threading.Thread(target=lambda: results.append(run.wait()), daemon=True)
    worker.start()
    time.sleep(0.05)
    assert not results, "wait() must block while the run is live"
    run.cancel()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert results and results[0].status == "cancelled"
    assert run.cancel_calls == 1


def test_hermetic_cancel_on_terminal_run_is_refused() -> None:
    """A finished run refuses cancel — the park ladder must not abort its bridge."""
    run = FakeCancellableRun()
    run.finish()
    with pytest.raises(RuntimeError):
        run.cancel()
    assert run.cancel_calls == 0


# ----------------------------------------------------------------- live spike


def _sqlite_files(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix in {".db", ".sqlite", ".sqlite3"}
    ]


def _run_status_rows(store_root: Path) -> list[tuple[str, str, str]]:
    """Return ``(db, table, status)`` for every row of a run-like table.

    Schema-agnostic on purpose: the bridge owns the store layout. Any table
    whose name mentions ``run`` and carries a ``status`` column is inspected.
    """
    rows: list[tuple[str, str, str]] = []
    for db in _sqlite_files(store_root):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            for table in tables:
                if "run" not in table.lower():
                    continue
                cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                if "status" not in cols:
                    continue
                for (status,) in conn.execute(
                    f'SELECT status FROM "{table}"'
                ).fetchall():
                    rows.append((db.name, table, str(status)))
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return rows


def _with_home(home: Path, fn: Any) -> Any:
    prev_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        return fn()
    finally:
        if prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev_home


@pytest.mark.skipif(
    not _cursor_api_key_available(),
    reason="live SDK creds absent — Leg 0a live spike skipped (quote in closeout)",
)
def test_live_cancel_run_then_resume_agent_continues_prior_work(
    tmp_path: Path,
) -> None:
    """Leg 0a: cancel after the first tool call, resume from a second bridge.

    Both bridges run under ``home_a``: the sqlite agent store is HOME-bound
    (store-A, ``test_cursor_sdk_store_locus_spike``), and the GIW resume path
    reuses the store-bearing dispatch HOME for exactly that reason. A fresh
    bridge process under the same HOME is the post-restart shape.
    """
    from cursor_sdk import Client
    from cursor_sdk.types import (
        AgentOptions,
        LocalAgentOptions,
        LocalAgentStoreConfig,
    )

    _inject_cursor_api_key()
    workspace = tmp_path / "wt"
    workspace.mkdir()
    store_root = tmp_path / "shared-store"
    store_root.mkdir()
    home_a = tmp_path / "home-a"
    home_a.mkdir()
    local_opts = LocalAgentOptions(
        cwd=str(workspace.resolve()),
        setting_sources=["user", "project"],
        store=LocalAgentStoreConfig(type="sqlite", root_dir=str(store_root)),
    )
    agent_options = AgentOptions(model="composer-2.5", mode="agent", local=local_opts)
    names = " ".join(f"{n}.txt" for n in _RESUME_MARKER_FILES)
    prompt = (
        "Create these five files in the current directory ONE AT A TIME, each "
        f"with a separate shell command, in this order: {names}. Write the "
        "file's own name as its content. After each file, reply with one line "
        "'created <name>'. Do not batch the commands."
    )

    observed: dict[str, Any] = {"tool_calls": 0, "cancel_error": None}

    def _create_and_cancel() -> tuple[str, str, list[str]]:
        client_a = Client.launch_bridge(
            workspace=str(workspace),
            state_root=str(store_root),
            timeout=120.0,
            local=local_opts,
        )
        try:
            agent = client_a.create_agent(agent_options)
            run = agent.send(prompt)
            deadline = time.monotonic() + _CANCEL_WAIT_TIMEOUT_S
            for event in run.events():
                message = event.sdk_message
                if message is not None and getattr(message, "type", "") == "tool_call":
                    observed["tool_calls"] += 1
                    if observed["tool_calls"] >= 1 and getattr(
                        message, "status", "completed"
                    ) in ("completed", "success", "finished", ""):
                        try:
                            run.cancel()
                        except Exception as exc:  # noqa: BLE001 — recorded, asserted below
                            observed["cancel_error"] = f"{type(exc).__name__}: {exc}"
                        break
                if time.monotonic() > deadline:
                    break
            result = run.wait()
            agent_id = getattr(agent, "agent_id", None) or getattr(agent, "id", None)
            assert agent_id, "agent identity missing after send"
            created = sorted(p.name for p in workspace.iterdir() if p.is_file())
            return str(agent_id), str(result.status), created
        finally:
            client_a.close()

    agent_id, cancel_status, created_before = _with_home(home_a, _create_and_cancel)
    store_rows = _run_status_rows(store_root) + _run_status_rows(home_a)
    print(
        "LEG0A cancel phase:",
        {
            "agent_id": agent_id,
            "cancel_status": cancel_status,
            "tool_calls_before_cancel": observed["tool_calls"],
            "cancel_error": observed["cancel_error"],
            "created_before": created_before,
            "store_run_rows": store_rows,
        },
    )

    assert observed["cancel_error"] is None, observed["cancel_error"]
    assert observed["tool_calls"] >= 1, "prompt produced no tool call to cancel behind"
    assert cancel_status in _TERMINAL_RUN_STATUSES, cancel_status
    assert cancel_status != "error", "cancel must not surface as an error terminal"

    running_rows = [r for r in store_rows if r[2].lower() in {"running", "in_progress"}]
    assert not running_rows, f"store left a run RUNNING after CancelRun: {running_rows}"

    def _resume_and_continue() -> tuple[str, str]:
        client_b = Client.launch_bridge(
            workspace=str(workspace),
            state_root=str(store_root),
            timeout=120.0,
            local=local_opts,
        )
        try:
            resumed = client_b.resume_agent(agent_id, agent_options)
            cont = resumed.send(
                "Continue where you left off. FIRST line of your reply: list the "
                "file names you already created in this conversation before this "
                "message (from your own memory of the prior turn). Then create "
                "any of the five that are still missing and stop."
            )
            cont_result = cont.wait()
            return str(cont_result.status), str(cont_result.result or "")
        finally:
            client_b.close()

    resume_status, resume_text = _with_home(home_a, _resume_and_continue)
    print(
        "LEG0A resume phase:",
        {"resume_status": resume_status, "resume_text": resume_text[:600]},
    )

    assert resume_status == "finished", (
        f"resume_agent continuation did not finish: status={resume_status} "
        f"cancel_status={cancel_status} tool_calls_before_cancel={observed['tool_calls']}"
    )
    lowered = resume_text.lower()
    references_prior = any(name in lowered for name in created_before) or any(
        f"{n}.txt" in lowered
        for n in _RESUME_MARKER_FILES[: max(1, len(created_before))]
    )
    assert references_prior, (
        "resumed turn does not reference prior work — bind falsified (R1 fallback):\n"
        f"created_before={created_before}\nresume_text={resume_text[:800]}"
    )
    created_after = sorted(p.name for p in workspace.iterdir() if p.is_file())
    assert set(created_before) <= set(created_after)
