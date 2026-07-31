"""Tests for git_integration_worker event envelope stamping."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.events import publish_lib_signal


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def settimeout(self, _timeout: float) -> None:
        return None

    def connect(self, _path: str) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_publish_lib_signal_includes_source_and_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSocket()
    monkeypatch.setattr(
        "services.git_integration_worker.events.socket.socket",
        lambda *_a, **_k: fake,
    )
    publish_lib_signal("frontier.sdk.worker.progress", {"dispatch_id": "d1"})
    assert len(fake.sent) == 1
    envelope = json.loads(fake.sent[0].decode())
    assert envelope["signal"] == "frontier.sdk.worker.progress"
    assert envelope["source"] == "git_integration_worker"
    assert envelope["timestamp"]
    assert envelope["payload"] == {"dispatch_id": "d1"}
