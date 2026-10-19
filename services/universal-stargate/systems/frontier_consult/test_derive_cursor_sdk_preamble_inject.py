"""Turn-0 preamble inject budget and plugin-census mount dedupe."""

from __future__ import annotations

from pathlib import Path

import pytest

from systems.frontier_consult.cursor_sdk_worker_dispatch import (
    _DISPATCH_PREAMBLE_INJECT_BUDGET_BYTES,
    derive_cursor_sdk_prompt_preamble,
)

_MOUNTED_PACKET = """
<invariants>
- agent_skill:reasoning-posture
</invariants>
"""
_UNMOUNTED_PACKET = """
<invariants>
- agent_skill:architecture-invariants
</invariants>
"""


class _Resolution:
    def __init__(self, block_md: str = "body") -> None:
        self.block_md = block_md
        self.dropped: list[dict[str, str]] = []


def test_implement_passes_budget_and_plugin_mount_slugs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def _resolve(*_args: object, **kwargs: object) -> _Resolution:
        seen.update(kwargs)
        return _Resolution("injected-body")

    monkeypatch.setattr(
        "agent_seat.inject_registry.resolve_injected_bodies",
        _resolve,
    )
    result = derive_cursor_sdk_prompt_preamble(
        handoff_contract="implement",
        pointer="Contract: implement\n",
        packet_text=_MOUNTED_PACKET,
    )
    assert result is not None
    assert "injected-body" in result
    assert seen["budget_bytes"] == _DISPATCH_PREAMBLE_INJECT_BUDGET_BYTES
    mounts = seen["provider_mount_slugs"]
    assert isinstance(mounts, frozenset)
    assert "reasoning-posture" in mounts


def test_bare_skips_plugin_mount_slugs(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _resolve(*_args: object, **kwargs: object) -> _Resolution:
        seen.update(kwargs)
        return _Resolution("bare-body")

    monkeypatch.setattr(
        "agent_seat.inject_registry.resolve_injected_bodies",
        _resolve,
    )
    derive_cursor_sdk_prompt_preamble(
        handoff_contract="bare",
        pointer="",
        packet_text=_MOUNTED_PACKET,
    )
    assert seen["provider_mount_slugs"] == frozenset()
    assert seen["budget_bytes"] == _DISPATCH_PREAMBLE_INJECT_BUDGET_BYTES


def test_unmounted_id_still_resolves_with_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "hub"
    (repo / "cursor-plugins" / "ulg-ecosystem" / "skills").mkdir(parents=True)
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(repo))

    called: dict[str, object] = {}

    def _resolve(*_args: object, **kwargs: object) -> _Resolution:
        called.update(kwargs)
        return _Resolution("arch-body")

    monkeypatch.setattr(
        "agent_seat.inject_registry.resolve_injected_bodies",
        _resolve,
    )
    result = derive_cursor_sdk_prompt_preamble(
        handoff_contract="implement",
        pointer="Contract: implement\n",
        packet_text=_UNMOUNTED_PACKET,
    )
    assert result is not None
    assert "arch-body" in result
    mounts = called["provider_mount_slugs"]
    assert isinstance(mounts, frozenset)
    assert mounts == frozenset()
