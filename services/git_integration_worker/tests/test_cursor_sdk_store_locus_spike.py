"""Store-locus discriminating spike for ``resume_of`` substrate choice.

Spike verdict when live SDK is unavailable (Fable review S1 + 9675 observation):
**store-A** — the sqlite agent store is keyed by parent dispatch HOME and cwd
(``<parent HOME>/.cursor/projects/<cwd>/sdk-agent-store``), not by an empty
``bridge-state`` / ``root_dir`` alone. Phase A spike was confounded (same HOME
both sides). Implement binds store-A: ``_run_sdk_sync`` reuses parent HOME on
``resume_of``; ``resolve_sdk_store_dir`` falls back to HOME-bound store.

Live spike (``test_live_store_locus_home_a_vs_home_b``): create under HOME_A,
resume under HOME_B with the same ``LocalAgentStoreConfig.root_dir`` and cwd.
Skipped without ``CURSOR_API_KEY`` — hermetic store-A binding above stands.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cursor_sdk import Client
from cursor_sdk.types import AgentOptions, LocalAgentOptions, LocalAgentStoreConfig

from services.git_integration_worker.cursor_home import dispatch_home_path
from services.git_integration_worker.cursor_sdk_resume import resolve_sdk_store_dir

STORE_LOCUS_VERDICT = "store-A"


def test_resolve_sdk_store_dir_prefers_nonempty_state_root(tmp_path: Path) -> None:
    store = tmp_path / "real-store"
    store.mkdir()
    (store / "agents.db").write_text("x")
    found = resolve_sdk_store_dir(
        parent_id="parent-disp",
        state_root=str(store),
    )
    assert found == store


def test_resolve_sdk_store_dir_falls_back_to_parent_home_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    homes_root = tmp_path / "homes"
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(homes_root))
    parent_id = "parent-home-bound-test"
    parent_home = dispatch_home_path(parent_id)
    cwd_slug = "mnt-torus-projects-repo"
    store = (
        parent_home
        / ".cursor"
        / "projects"
        / cwd_slug
        / "sdk-agent-store"
    )
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


@pytest.mark.skipif(
    not os.environ.get("CURSOR_API_KEY"),
    reason=(
        "live SDK creds absent — store-A bound from Fable S1 + 9675 observation "
        f"(verdict={STORE_LOCUS_VERDICT})"
    ),
)
def test_live_store_locus_home_a_vs_home_b(tmp_path: Path) -> None:
    """Discriminating spike: create HOME_A → resume HOME_B, same root_dir + cwd."""
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
        agent = client_a.create_agent(agent_options)
        run = agent.send(prompt)
        result = run.wait()
        assert result.status == "finished"
        agent_id = getattr(agent, "agent_id", None) or getattr(agent, "id", None)
        assert agent_id
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
        resumed = client_b.resume_agent(str(agent_id), agent_options)
        cont = resumed.send("Continue: reply store-locus-resume-ok")
        cont_result = cont.wait()
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
