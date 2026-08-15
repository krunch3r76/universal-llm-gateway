"""Unit tests for project-ask helpers (no CDP)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_bundles import cdp_registry as reg
from claude_bundles import project_ask_abort as abort
from claude_bundles.chat_session_hygiene import _page_score
from claude_bundles.project_ask import (
    archive_harvest,
    read_archive_execution_id,
    strip_thinking_prefix,
    submit_control_names,
)
from claude_bundles.project_chrome import project_url

pytestmark = pytest.mark.offline


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path):
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9226))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    return root


def _noop_launch(port: int, profile):
    profile.mkdir(parents=True, exist_ok=True)
    return 1


def test_project_url_shape() -> None:
    uuid = "019f6917-2ab2-772c-a1ec-f88434b08e32"
    assert project_url(uuid) == f"https://claude.ai/cowork/project/{uuid}"


def test_page_score_prefers_chat_over_project() -> None:
    assert _page_score("https://claude.ai/chat/abc") > _page_score(
        "https://claude.ai/cowork/project/019f6917-2ab2-772c-a1ec-f88434b08e32"
    )


def test_strip_thinking_prefix() -> None:
    raw = (
        "Thinking about concerns with this request\n"
        "Thinking about concerns with this request\n\n"
        "ASK_HARNESS_OK\nPROJECT=SCC\n"
    )
    cleaned = strip_thinking_prefix(raw)
    assert cleaned.startswith("ASK_HARNESS_OK")
    assert "Thinking about" not in cleaned


def test_submit_control_names_cowork_before_chat() -> None:
    """Friction 24609 — Cowork Start task precedes Chat Send message."""
    names = submit_control_names()
    assert names[0] == "Start task"
    assert "Send message" in names


def test_archive_harvest_same_execution_growth_rewrites(tmp_path: Path) -> None:
    archive = tmp_path / "harvest.md"
    execution_id = "exec" + "a" * 28
    uri = archive_harvest(
        body="first",
        url="https://claude.ai/new",
        project_uuid="",
        model={"ok": True},
        attested_model="opus-4.8",
        archive_path=str(archive),
        execution_id=execution_id,
    )
    assert uri.startswith("file://")
    assert read_archive_execution_id(str(archive)) == execution_id
    archive_harvest(
        body="second body",
        url="https://claude.ai/new",
        project_uuid="",
        model={"ok": True},
        attested_model="opus-4.8",
        archive_path=str(archive),
        execution_id=execution_id,
    )
    assert "second body" in archive.read_text(encoding="utf-8")


def test_archive_harvest_foreign_execution_refused(tmp_path: Path) -> None:
    archive = tmp_path / "harvest.md"
    archive_harvest(
        body="first",
        url="https://claude.ai/new",
        project_uuid="",
        model={"ok": True},
        attested_model="opus-4.8",
        archive_path=str(archive),
        execution_id="exec" + "a" * 28,
    )
    with pytest.raises(RuntimeError, match="foreign execution"):
        archive_harvest(
            body="clobber",
            url="https://claude.ai/new",
            project_uuid="",
            model={"ok": True},
            attested_model="opus-4.8",
            archive_path=str(archive),
            execution_id="exec" + "b" * 28,
        )


def test_emit_detached_status(capsys: pytest.CaptureFixture[str]) -> None:
    abort.emit_detached_status("abc123")
    out = capsys.readouterr().out
    assert "status=detached_remote_running registration_id=abc123" in out


def test_abort_cleanup_non_owner_emits_detached_not_kill(
    isolated_registry, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    r = reg.register_lane(
        holder="remote-driver",
        purpose="ask",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg._release_driver_lock(r.registration_id)
    active = reg._load_active()
    active[r.registration_id] = dict(active[r.registration_id])
    active[r.registration_id]["holder_pid"] = os.getpid() + 99999
    monkeypatch.setattr(reg._store, "load_active", lambda: active)
    monkeypatch.setattr(reg, "is_driver_lock_held", lambda _rid: True)
    killed: list[str] = []
    monkeypatch.setattr(abort, "bounded_stop_via_cdp", lambda _url: killed.append("stop"))
    monkeypatch.setattr(abort, "deregister_on_exit", lambda *_a, **_k: killed.append("kill"))
    abort._ABORT_DONE = False
    abort.abort_cleanup(r, purpose="ask")
    assert killed == []
    assert "status=detached_remote_running" in capsys.readouterr().out


def test_abort_cleanup_orphan_reap_noop_on_port_reassign(
    isolated_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = reg.register_lane(
        holder="remote-driver",
        purpose="ask",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg._release_driver_lock(r.registration_id)
    killed: list[str] = []
    monkeypatch.setattr(abort, "bounded_stop_via_cdp", lambda _url: killed.append("stop"))
    monkeypatch.setattr(abort, "deregister_on_exit", lambda *_a, **_k: killed.append("kill"))
    monkeypatch.setattr(abort, "registration_owns_port", lambda *_a, **_k: False)
    abort._ABORT_DONE = False
    abort.abort_cleanup(r, purpose="ask")
    assert killed == []


def test_abort_cleanup_owner_still_kills(
    isolated_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = reg.register_lane(
        holder="owner",
        purpose="ask",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    killed: list[str] = []
    monkeypatch.setattr(
        abort,
        "bounded_stop_via_cdp",
        lambda _url: killed.append("stop")
        or abort.AttestResult(has_stop=False, probe_ok=True),
    )
    monkeypatch.setattr(abort, "deregister_on_exit", lambda *_a, **_k: killed.append("kill"))
    abort._ABORT_DONE = False
    abort.abort_cleanup(r, purpose="ask")
    assert killed == ["stop", "kill"]
    reg._release_driver_lock(r.registration_id)


def test_registration_owns_port_rejects_reassigned(
    isolated_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert abort.registration_owns_port(r.registration_id, r.port)
    active = reg._load_active()
    active[r.registration_id] = dict(active[r.registration_id])
    active[r.registration_id]["port"] = r.port + 1
    monkeypatch.setattr(reg._store, "load_active", lambda: active)
    assert not abort.registration_owns_port(r.registration_id, r.port)
    reg._release_driver_lock(r.registration_id)
