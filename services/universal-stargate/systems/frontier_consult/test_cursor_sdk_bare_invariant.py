"""Bare cursor-sdk preamble injects packet-named invariant bodies."""

from __future__ import annotations

import pytest

from systems.frontier_consult.cursor_sdk_worker_dispatch import (
    derive_cursor_sdk_prompt_preamble,
)

_PACKET = """
<invariants>
- agent_skill:architecture-invariants
</invariants>
"""
_BODY = "architecture-invariants-body-floor"


class _Resolution:
    def __init__(self, block_md: str) -> None:
        self.block_md = block_md


def _assert_bare_invariant(
    result: str | None,
    called_ids: tuple[str, ...] | None,
) -> None:
    """Binding injection. Fails when the early None return drops the body."""
    assert called_ids == ("agent_skill:architecture-invariants",)
    assert result is not None
    assert _BODY in result
    assert "Execute this task NOW" not in result


def test_bare_injects_packet_invariants(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, tuple[str, ...]] = {}

    def _resolve(*_args: object, **kwargs: object) -> _Resolution:
        ids = kwargs.get("packet_invariant_ids")
        assert isinstance(ids, tuple)
        called["ids"] = ids
        return _Resolution(_BODY)

    monkeypatch.setattr(
        "agent_seat.inject_registry.resolve_injected_bodies",
        _resolve,
    )
    result = derive_cursor_sdk_prompt_preamble(
        handoff_contract="bare",
        pointer="Contract: implement\n",
        packet_text=_PACKET,
    )
    _assert_bare_invariant(result, called.get("ids"))
    with pytest.raises(AssertionError):
        _assert_bare_invariant(None, None)


def test_implement_still_prepends_invariant_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _resolve(*_args: object, **_kwargs: object) -> _Resolution:
        return _Resolution(_BODY)

    monkeypatch.setattr(
        "agent_seat.inject_registry.resolve_injected_bodies",
        _resolve,
    )
    result = derive_cursor_sdk_prompt_preamble(
        handoff_contract="implement",
        pointer="Contract: implement\n",
        packet_text=_PACKET,
    )
    assert result is not None
    assert _BODY in result
    assert "Execute this task NOW" in result
