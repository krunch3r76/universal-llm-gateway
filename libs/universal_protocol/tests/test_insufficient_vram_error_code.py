"""INSUFFICIENT_VRAM is permanent (thread 1236 F4)."""

from universal_protocol import ErrorCode, is_retryable


def test_insufficient_vram_not_retryable() -> None:
    assert is_retryable(ErrorCode.INSUFFICIENT_VRAM) is False


def test_resource_unavailable_still_retryable_in_metadata() -> None:
    assert is_retryable(ErrorCode.RESOURCE_UNAVAILABLE) is True
