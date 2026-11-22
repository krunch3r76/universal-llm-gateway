"""Native helper allow-set and deny spool policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_native_helper_policy import (
    NativeHelperPolicy,
    append_native_helper_attempt,
    harvest_native_helper_attempts,
    resolve_native_helper_policy,
)


def test_default_deny_unknown_kind() -> None:
    policy = resolve_native_helper_policy(packet_text="", contract="implement")
    assert policy.allows("generalPurpose") is False
    assert policy.allows("unspecified") is False


def test_explore_allowed_when_packet_opt_in() -> None:
    packet = "native_helper_allow: explore\n"
    policy = resolve_native_helper_policy(packet_text=packet, contract="implement")
    assert policy.allows("explore") is True
    assert policy.allows("generalPurpose") is False


def test_packet_opt_in_intersect_allowlist() -> None:
    policy = resolve_native_helper_policy(
        packet_text="native_helper_allow: explore, generalPurpose\n",
        contract="implement",
        enforcement="enforced",
    )
    assert policy.allows("explore") is True
    assert policy.allows("generalPurpose") is False


def test_denial_recorded_harmless(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services.git_integration_worker import cursor_sdk_native_helper_policy as mod

    events: list = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_limits_events.emit_frontier_event",
        lambda event: events.append(event),
    )
    monkeypatch.setattr(mod, "native_helper_spool_dir", lambda: tmp_path)
    append_native_helper_attempt(
        "d-test",
        {"kind": "generalPurpose", "denied": True},
    )
    attempts = harvest_native_helper_attempts("d-test")
    assert len(attempts) == 1
    assert attempts[0]["denied"] is True
    denied = [e for e in events if e.signal == "frontier.sdk.native.helper.denied"]
    assert len(denied) == 1
    assert denied[0].payload["dispatch_id"] == "d-test"
    assert denied[0].payload["kind"] == "generalPurpose"


def test_unenforced_stated_when_sdk_outside_probe_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.cursor_sdk_native_helper_policy import (
        resolve_native_helper_enforcement,
    )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_native_helper_policy.installed_cursor_sdk_version",
        lambda: "0.0.1",
    )
    assert resolve_native_helper_enforcement() == "unenforced"


def test_breach_emitted_for_disallowed_harvested_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    from services.git_integration_worker import cursor_sdk_native_helper_policy as mod
    from services.git_integration_worker.cursor_sdk_subagent_capture import (
        SUBAGENTS_SURFACE,
    )

    events: list = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_limits_events.emit_frontier_event",
        lambda event: events.append(event),
    )
    policy = NativeHelperPolicy(
        allow_kinds=frozenset({"explore"}),
        enforcement="enforced",
    )
    manifest = EffectsManifest(
        dispatch_id="d-breach",
        thread_id="24956",
        surfaces={
            SUBAGENTS_SURFACE: SurfaceSection(
                surface=SUBAGENTS_SURFACE,
                source="stream",
                entries=[
                    EffectEntry(
                        op="Task",
                        target="generalPurpose",
                        identity="tc-1",
                    )
                ],
            )
        },
    )
    mod.emit_native_helper_breaches_for_harvest(
        dispatch_id="d-breach",
        thread_id="24956",
        policy=policy,
        attempts=[],
        manifest=manifest,
    )
    breach = [e for e in events if e.signal == "frontier.sdk.native.helper.breach"]
    assert len(breach) == 1
    assert breach[0].payload["kind"] == "generalpurpose"


def test_breach_skipped_when_unenforced(monkeypatch: pytest.MonkeyPatch) -> None:
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    from services.git_integration_worker import cursor_sdk_native_helper_policy as mod
    from services.git_integration_worker.cursor_sdk_subagent_capture import (
        SUBAGENTS_SURFACE,
    )

    events: list = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_limits_events.emit_frontier_event",
        lambda event: events.append(event),
    )
    policy = NativeHelperPolicy(
        allow_kinds=frozenset({"explore"}),
        enforcement="unenforced",
    )
    manifest = EffectsManifest(
        dispatch_id="d-no-breach",
        thread_id="1",
        surfaces={
            SUBAGENTS_SURFACE: SurfaceSection(
                surface=SUBAGENTS_SURFACE,
                source="stream",
                entries=[
                    EffectEntry(
                        op="Task",
                        target="generalPurpose",
                        identity="tc-1",
                    )
                ],
            )
        },
    )
    mod.emit_native_helper_breaches_for_harvest(
        dispatch_id="d-no-breach",
        thread_id="1",
        policy=policy,
        attempts=[],
        manifest=manifest,
    )
    assert not [e for e in events if e.signal == "frontier.sdk.native.helper.breach"]


def test_install_writes_policy_file(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_sdk_native_helper_policy import (
        install_native_helper_hook,
    )

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    policy = NativeHelperPolicy(
        allow_kinds=frozenset({"explore"}),
        enforcement="enforced",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_sdk_native_helper_policy.resolve_native_helper_enforcement",
            lambda: "enforced",
        )
        mp.setattr(
            "services.git_integration_worker.cursor_sdk_native_helper_policy.native_helper_hook_script_path",
            lambda: tmp_path / "hook.py",
        )
        (tmp_path / "hook.py").write_text("# stub\n", encoding="utf-8")
        install_native_helper_hook(cursor_dir, policy=policy)
    data = json.loads((cursor_dir / "hooks.json").read_text(encoding="utf-8"))
    assert "preToolUse" in data["hooks"]
    assert data["hooks"]["preToolUse"][0]["failClosed"] is True
