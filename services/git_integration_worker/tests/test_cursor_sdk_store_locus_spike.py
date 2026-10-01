"""Store-locus discriminating spike for ``resume_of`` substrate choice.

Spike verdict when live SDK is unavailable (Fable review S1 + 9675 observation):
**store-A** — the sqlite agent store is keyed by parent dispatch HOME and cwd
(``<parent HOME>/.cursor/projects/<cwd>/sdk-agent-store``), not by an empty
``bridge-state`` / ``root_dir`` alone. Phase A spike was confounded (same HOME
both sides). Implement binds store-A: ``_run_sdk_sync`` reuses parent HOME on
``resume_of``; ``resolve_sdk_store_dir`` falls back to HOME-bound store.

Live spike (``test_live_store_locus_home_a_vs_home_b``): create under HOME_A,
resume under HOME_B with the same ``LocalAgentStoreConfig.root_dir`` and cwd.
Skipped without a Cursor key in ``CURSOR_API_KEY`` or
``~/.gateway/secrets.env`` (GIW's unit feed). The spike remaps ``HOME``, so
``auth.json`` cannot carry the session — the process env must hold the key.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cursor_sdk import Client
from cursor_sdk.types import AgentOptions, LocalAgentOptions, LocalAgentStoreConfig

from services.git_integration_worker import cursor_home
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_home import dispatch_home_path
from services.git_integration_worker.cursor_sdk_store_locus import (
    resolve_sdk_store_dir,
    resolve_store_bearing_dispatch_id,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

STORE_LOCUS_VERDICT = "store-A"
_SECRETS_ENV = Path.home() / ".gateway" / "secrets.env"


def _read_cursor_api_key() -> str:
    """Resolve GIW's Cursor key without printing it. Env, then secrets.env."""
    from_env = os.environ.get("CURSOR_API_KEY", "").strip()
    if from_env:
        return from_env
    if not _SECRETS_ENV.is_file():
        return ""
    for raw in _SECRETS_ENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep and name.strip() == "CURSOR_API_KEY":
            return value.strip().strip("'\"")
    return ""


def _cursor_api_key_available() -> bool:
    return bool(_read_cursor_api_key())


def _inject_cursor_api_key() -> None:
    """Put GIW's key in this process. Spike remaps HOME; auth.json is then invisible."""
    key = _read_cursor_api_key()
    if key:
        os.environ["CURSOR_API_KEY"] = key


def test_resolve_sdk_store_dir_prefers_nonempty_state_root(tmp_path: Path) -> None:
    store = tmp_path / "real-store"
    store.mkdir()
    (store / "agents.db").write_text("x")
    found = resolve_sdk_store_dir(
        parent_id="parent-disp",
        state_root=str(store),
    )
    assert found == store


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "ledger-data"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _pin_homes(monkeypatch: pytest.MonkeyPatch, homes_root: Path) -> None:
    monkeypatch.setattr(cursor_home, "_DISPATCH_HOME_ROOT", homes_root)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(homes_root))


def _insert_dispatch(
    *,
    dispatch_id: str,
    state_root: str | None,
    resume_of: str | None = None,
    sdk_agent_id: str = "agent",
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="thread-owner",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message=f"msg-{dispatch_id}",
    )
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            "message_present, status, state_root, sdk_agent_id, resume_of) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (
                dispatch_id,
                ledger.fingerprint(req),
                req.thread_id,
                req.execution_id,
                "composer-2.5",
                "completed",
                state_root,
                sdk_agent_id,
                resume_of,
            ),
        )


def _home_store(dispatch_id: str) -> Path:
    store = (
        dispatch_home_path(dispatch_id)
        / ".cursor"
        / "projects"
        / "mnt-torus-projects-repo"
        / "sdk-agent-store"
    )
    store.mkdir(parents=True, exist_ok=True)
    (store / "agents.db").write_text("x")
    return store


def test_resolve_sdk_store_dir_falls_back_to_parent_home_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    homes_root = tmp_path / "homes"
    _pin_homes(monkeypatch, homes_root)
    parent_id = "parent-home-bound-test"
    parent_home = dispatch_home_path(parent_id)
    cwd_slug = "mnt-torus-projects-repo"
    store = parent_home / ".cursor" / "projects" / cwd_slug / "sdk-agent-store"
    store.mkdir(parents=True, exist_ok=True)
    (store / "agents.db").write_text("x")
    empty_bridge = tmp_path / "empty-bridge-state"
    empty_bridge.mkdir()
    found = resolve_sdk_store_dir(
        parent_id=parent_id,
        state_root=str(empty_bridge),
    )
    assert found == store
    assert STORE_LOCUS_VERDICT == "store-A"


def test_store_owner_is_ancestor_when_state_root_was_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rewritten state_root on the child names the ancestor store; owner is the ancestor."""
    _pin_homes(monkeypatch, tmp_path / "homes")
    store = _home_store("dispatch-a")
    _insert_dispatch(
        dispatch_id="dispatch-a", state_root=str(store), sdk_agent_id="agent-a"
    )
    _insert_dispatch(
        dispatch_id="dispatch-b",
        state_root=str(store),
        resume_of="dispatch-a",
        sdk_agent_id="agent-b",
    )
    assert resolve_store_bearing_dispatch_id(parent_id="dispatch-b") == "dispatch-a"


def test_store_owner_ignores_stray_store_in_intermediate_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray sdk-agent-store under the intermediate HOME does not become the owner."""
    _pin_homes(monkeypatch, tmp_path / "homes")
    store = _home_store("dispatch-a")
    stray = (
        dispatch_home_path("dispatch-b")
        / ".cursor"
        / "projects"
        / "mnt-torus-projects-repo"
        / "sdk-agent-store"
    )
    stray.mkdir(parents=True, exist_ok=True)
    (stray / "agents.db").write_text("stray")
    _insert_dispatch(
        dispatch_id="dispatch-a", state_root=str(store), sdk_agent_id="agent-a"
    )
    _insert_dispatch(
        dispatch_id="dispatch-b",
        state_root=str(store),
        resume_of="dispatch-a",
        sdk_agent_id="agent-b",
    )
    assert resolve_store_bearing_dispatch_id(parent_id="dispatch-b") == "dispatch-a"


def test_store_owner_falls_back_to_parent_when_store_outside_every_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store outside every lineage HOME returns parent_id and emits mode=external."""
    _pin_homes(monkeypatch, tmp_path / "homes")
    outside = tmp_path / "outside-store"
    outside.mkdir()
    (outside / "agents.db").write_text("x")
    parent_id = "parent-external"
    _insert_dispatch(dispatch_id=parent_id, state_root=str(outside))
    emitted: list[object] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_resume_store_events.emit_frontier_event",
        lambda event: emitted.append(event),
    )
    assert resolve_store_bearing_dispatch_id(parent_id=parent_id) == parent_id
    assert len(emitted) == 1
    event = emitted[0]
    assert event.signal == "giw.resume.store.owner.resolved"
    assert event.payload["mode"] == "external"
    assert event.payload["parent_id"] == parent_id
    assert event.payload["owner_dispatch_id"] == parent_id
    assert event.payload["store_path"] == str(outside)


def test_store_owner_home_scan_without_ledger_row_is_rescanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A HOME scan with no parent ledger row emits mode=rescanned, not external."""
    _pin_homes(monkeypatch, tmp_path / "homes")
    parent_id = "parent-no-ledger-row"
    _home_store(parent_id)
    emitted: list[object] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_resume_store_events.emit_frontier_event",
        lambda event: emitted.append(event),
    )
    assert resolve_store_bearing_dispatch_id(parent_id=parent_id) == parent_id
    assert len(emitted) == 1
    event = emitted[0]
    assert event.signal == "giw.resume.store.owner.resolved"
    assert event.payload["mode"] == "rescanned"
    assert event.payload["owner_dispatch_id"] == parent_id
    assert event.payload["parent_id"] == parent_id


def test_first_generation_owner_selection_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single dispatch whose HOME contains its store remains the owner."""
    _pin_homes(monkeypatch, tmp_path / "homes")
    parent_id = "dispatch-root"
    store = _home_store(parent_id)
    _insert_dispatch(dispatch_id=parent_id, state_root=str(store))
    assert resolve_store_bearing_dispatch_id(parent_id=parent_id) == parent_id


@pytest.mark.skipif(
    not _cursor_api_key_available(),
    reason=(
        "live SDK creds absent — store-A bound from Fable S1 + 9675 observation "
        f"(verdict={STORE_LOCUS_VERDICT})"
    ),
)
def test_live_store_locus_home_a_vs_home_b(tmp_path: Path) -> None:
    """Discriminating spike: create HOME_A → resume HOME_B, same root_dir + cwd."""
    _inject_cursor_api_key()
    workspace = tmp_path / "wt"
    workspace.mkdir()
    store_root = tmp_path / "shared-store"
    store_root.mkdir()
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    home_a.mkdir()
    home_b.mkdir()
    store_cfg = LocalAgentStoreConfig(type="sqlite", root_dir=str(store_root))
    local_opts = LocalAgentOptions(
        cwd=str(workspace.resolve()),
        setting_sources=["user", "project"],
        store=store_cfg,
    )
    agent_options = AgentOptions(
        model="composer-2.5",
        mode="agent",
        local=local_opts,
    )
    prompt = "Reply with exactly: store-locus-spike-ok"

    prev_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home_a)
    try:
        client_a = Client.launch_bridge(
            workspace=str(workspace),
            state_root=str(store_root),
            timeout=120.0,
            local=local_opts,
        )
        # close() under finally: a failed assert here used to strand the Node
        # bridge, which then outlived the pytest process (assertion 31706).
        try:
            agent = client_a.create_agent(agent_options)
            run = agent.send(prompt)
            result = run.wait()
            assert result.status == "finished"
            agent_id = getattr(agent, "agent_id", None) or getattr(agent, "id", None)
            assert agent_id
        finally:
            client_a.close()
    finally:
        if prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev_home

    os.environ["HOME"] = str(home_b)
    try:
        client_b = Client.launch_bridge(
            workspace=str(workspace),
            state_root=str(store_root),
            timeout=120.0,
            local=local_opts,
        )
        try:
            resumed = client_b.resume_agent(str(agent_id), agent_options)
            cont = resumed.send("Continue: reply store-locus-resume-ok")
            cont_result = cont.wait()
        except Exception as exc:
            if type(exc).__name__ in {"AgentNotFoundError", "NotFoundError"}:
                assert STORE_LOCUS_VERDICT == "store-A"
                return
            raise
        finally:
            client_b.close()
    finally:
        if prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev_home

    if cont_result.status != "finished":
        pytest.fail(
            "store-B (root_dir honored across HOMEs): resume failed — "
            "implement should pass store correctly instead of HOME reuse"
        )
    pytest.fail(
        "store-B verdict: root_dir alone resumed across HOMEs — "
        "revisit HOME reuse substrate binding"
    )
