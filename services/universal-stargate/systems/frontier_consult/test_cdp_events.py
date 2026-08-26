"""Unit tests for CDP event publish instrumentation."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from universal_event_bus import Event

from systems.frontier_consult.cdp_events import (
    CdpGenerateAdmitted,
    CdpGenerateSubmitted,
    publish_cdp_event,
    publish_cdp_kwargs,
)


def _publish_log_records(caplog: pytest.LogCaptureFixture) -> list[Any]:
    return [rec for rec in caplog.records if "cdp.event.publish" in rec.message]


@pytest.mark.parametrize(
    ("expected_outcome", "setup_get_proxy"),
    [
        (
            "proxy_uninitialized",
            lambda monkeypatch: monkeypatch.setattr(
                "systems.proxy.dependencies.get_proxy",
                lambda: (_ for _ in ()).throw(
                    RuntimeError(
                        "Proxy not initialized. Call init_proxy() during app startup."
                    )
                ),
            ),
        ),
        (
            "bus_none",
            lambda monkeypatch: monkeypatch.setattr(
                "systems.proxy.dependencies.get_proxy",
                lambda: MagicMock(event_bus=None),
            ),
        ),
    ],
)
def test_publish_swallow_paths_no_raise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    expected_outcome: str,
    setup_get_proxy: Any,
) -> None:
    caplog.set_level(logging.DEBUG)
    setup_get_proxy(monkeypatch)
    event = CdpGenerateAdmitted(
        request_id="req-swallow",
        execution_id="exec-swallow",
        model="cdp/opus-5",
        thread_id="thread-1",
    )
    publish_cdp_event(event)
    logs = _publish_log_records(caplog)
    assert len(logs) == 1
    assert f"outcome={expected_outcome}" in logs[0].message
    assert "signal=cdp.generate.admitted" in logs[0].message
    assert "request_id=req-swallow" in logs[0].message
    assert "execution_id=exec-swallow" in logs[0].message


def test_publish_exception_no_raise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    def _raise_on_publish(_event: Event) -> None:
        raise OSError("uds dead")

    mock_bus = MagicMock()
    mock_bus.publish_from_sync = _raise_on_publish
    monkeypatch.setattr(
        "systems.proxy.dependencies.get_proxy",
        lambda: MagicMock(event_bus=mock_bus),
    )
    publish_cdp_event(
        CdpGenerateAdmitted(
            request_id="req-pub-exc",
            execution_id="exec-pub-exc",
            model="cdp/opus-5",
            thread_id="thread-1",
        )
    )
    logs = _publish_log_records(caplog)
    assert len(logs) == 1
    assert "outcome=publish_exception" in logs[0].message
    assert "exc_type=OSError" in logs[0].message


def test_factory_exception_no_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    def _raising_factory(**_kwargs: Any) -> Event:
        raise ValueError("deliberately bad factory")

    publish_cdp_kwargs(
        _raising_factory,
        request_id="req-factory",
        execution_id="exec-factory",
        model="cdp/opus-5",
    )
    logs = _publish_log_records(caplog)
    assert len(logs) == 1
    assert "outcome=factory_exception" in logs[0].message
    assert "exc_type=ValueError" in logs[0].message
    assert "kwarg_names=execution_id,model,request_id" in logs[0].message


def test_f6_none_thread_id_and_satellite_id_accepted() -> None:
    admitted = CdpGenerateAdmitted(
        request_id="req-f6",
        execution_id="exec-f6",
        model="cdp/opus-5",
        thread_id=None,  # type: ignore[arg-type]
    )
    assert admitted.signal == "cdp.generate.admitted"
    assert admitted.payload["thread_id"] is None

    submitted = CdpGenerateSubmitted(
        request_id="req-f6",
        execution_id="exec-f6",
        satellite_execution_id=None,  # type: ignore[arg-type]
        model="cdp/opus-5",
    )
    assert submitted.signal == "cdp.generate.submitted"
    assert submitted.payload["satellite_execution_id"] is None


def test_healthy_emit_capture(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    captured: list[Event] = []

    mock_bus = MagicMock()
    mock_bus.publish_from_sync = captured.append
    monkeypatch.setattr(
        "systems.proxy.dependencies.get_proxy",
        lambda: MagicMock(event_bus=mock_bus),
    )

    publish_cdp_kwargs(
        CdpGenerateAdmitted,
        request_id="req-healthy",
        execution_id="exec-healthy",
        model="cdp/opus-5",
        thread_id="thread-healthy",
    )
    publish_cdp_kwargs(
        CdpGenerateSubmitted,
        request_id="req-healthy",
        execution_id="exec-healthy",
        satellite_execution_id="sat-healthy",
        model="cdp/opus-5",
    )

    assert len(captured) == 2
    assert captured[0].signal == "cdp.generate.admitted"
    assert set(captured[0].payload.keys()) == {
        "request_id",
        "execution_id",
        "model",
        "thread_id",
    }
    assert captured[1].signal == "cdp.generate.submitted"
    assert set(captured[1].payload.keys()) == {
        "request_id",
        "execution_id",
        "satellite_execution_id",
        "model",
    }

    ok_logs = [
        rec
        for rec in _publish_log_records(caplog)
        if "outcome=ok" in rec.message
    ]
    assert len(ok_logs) == 2


def test_cdp_generate_admitted_with_topic_includes_key() -> None:
    admitted = CdpGenerateAdmitted(
        request_id="req-topic",
        execution_id="exec-topic",
        model="cdp/opus-5",
        thread_id="thread-topic",
        topic="ULG gains glanceable CDP topics",
    )
    assert admitted.payload["topic"] == "ULG gains glanceable CDP topics"


def test_cdp_generate_admitted_without_topic_omits_key() -> None:
    admitted = CdpGenerateAdmitted(
        request_id="req-notopic",
        execution_id="exec-notopic",
        model="cdp/opus-5",
        thread_id="thread-notopic",
    )
    assert "topic" not in admitted.payload


def test_healthy_emit_with_topic_capture(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    captured: list[Event] = []

    mock_bus = MagicMock()
    mock_bus.publish_from_sync = captured.append
    monkeypatch.setattr(
        "systems.proxy.dependencies.get_proxy",
        lambda: MagicMock(event_bus=mock_bus),
    )

    publish_cdp_kwargs(
        CdpGenerateAdmitted,
        request_id="req-healthy-topic",
        execution_id="exec-healthy-topic",
        model="cdp/opus-5",
        thread_id="thread-healthy-topic",
        topic="Paint the CSE chat",
    )

    assert len(captured) == 1
    assert captured[0].payload["topic"] == "Paint the CSE chat"
