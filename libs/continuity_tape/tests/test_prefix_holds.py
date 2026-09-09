"""PREFIX-EXTEND checks for messages-v1 succession (G6 P1.5)."""

from __future__ import annotations

import pytest

from cortex_store.verbatim_succession import prefix_holds
from continuity_tape.messages import seal_messages_sha256

pytestmark = pytest.mark.offline


def _msg(role: str, content: str, turn_index: int) -> dict[str, object]:
    return {"role": role, "content": content, "turn_index": turn_index}


def test_prefix_holds_messages_v1_exact_prefix() -> None:
    sealed = [
        _msg("user", "hi", 1),
        _msg("assistant", "ok", 1),
    ]
    extended = sealed + [_msg("user", "more", 2), _msg("assistant", "ack", 2)]
    assert prefix_holds("messages-v1", sealed, extended) is True


def test_prefix_holds_messages_v1_partial_last_message() -> None:
    sealed = [_msg("user", "hello", 1), _msg("assistant", "hel", 1)]
    extended = [_msg("user", "hello", 1), _msg("assistant", "hello world", 1)]
    assert prefix_holds("messages-v1", sealed, extended) is True


def test_prefix_holds_messages_v1_role_divergence() -> None:
    sealed = [_msg("user", "hi", 1)]
    diverged = [_msg("assistant", "hi", 1)]
    assert prefix_holds("messages-v1", sealed, diverged) is False


def test_prefix_holds_messages_v1_shorter_new_list() -> None:
    sealed = [_msg("user", "hi", 1), _msg("assistant", "ok", 1)]
    shorter = [_msg("user", "hi", 1)]
    assert prefix_holds("messages-v1", sealed, shorter) is False


def test_seal_fingerprint_includes_turn_index() -> None:
    base = [_msg("user", "same", 1), _msg("assistant", "same", 1)]
    shifted = [_msg("user", "same", 2), _msg("assistant", "same", 2)]
    assert seal_messages_sha256(base) != seal_messages_sha256(shifted)
