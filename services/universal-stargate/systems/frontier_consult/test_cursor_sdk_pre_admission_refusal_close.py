"""AC3: auto-minted worker threads close after pre-admission GIW refusal."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from .cursor_sdk_generate import _finish_prepared_dispatch
from .cursor_sdk_prepared_handle import PreparedCursorSdkHandle


def _minted_handle(**overrides: object) -> PreparedCursorSdkHandle:
    base = {
        "request_id": "req-pre",
        "execution_id": "exec-pre",
        "dispatch_id": "disp-pre",
        "thread_id": "22690",
        "resolved_model": "composer-2.5",
        "role": "cursor-sdk",
        "family": "cursor",
        "platform": "cursor",
        "to_agent": "cursor-sdk",
        "handoff_contract": "implement",
        "packet_path": "/tmp/packet.md",
        "message": None,
        "caller_agent": "frontier-admit",
        "read_only": False,
        "aligned_knobs": None,
        "prompt_preamble": None,
        "thread_subject": "worker",
        "pointer_body": "go",
        "effective_bus_lifecycle": "ephemeral",
        "parent_dispatch_thread_id": None,
        "dispatch_thread_id": "22704",
        "density_triage": None,
        "review_opt_out_reason_code": None,
        "auto_review_child": False,
        "auto_review_defaulted": False,
        "claimed_via_atomic": False,
        "admitted": True,
        "alignment_warnings": (),
        "knob_resolution": (),
        "lane": "B",
        "worker_thread_reused": False,
    }
    base.update(overrides)
    return PreparedCursorSdkHandle(**base)


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._payload


def _routing_bus_and_giw_client(
    *,
    giw_status: int,
    giw_body: dict[str, Any],
    bus_journal: list[tuple[str, str, dict[str, Any] | None]],
) -> type:
    class _Client:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def post(
            self,
            path: str,
            *,
            json: dict[str, Any] | None = None,
            headers: dict[str, Any] | None = None,
        ) -> _Resp:
            del headers
            if "/api/v1/cursor/dispatch" in path:
                bus_journal.append(("giw", path, json))
                return _Resp(giw_status, giw_body)
            bus_journal.append(("post", path, json))
            if "/dispatch-terminate" in path:
                return _Resp(
                    200,
                    {
                        "id": (json or {}).get("execution_id", ""),
                        "status": "closed",
                        "dispatch_links": [
                            {
                                "execution_id": (json or {})["execution_id"],
                                "terminal_status": (json or {})["terminal_status"],
                            }
                        ],
                    },
                )
            return _Resp(201, {"ok": True})

        async def patch(
            self,
            path: str,
            *,
            json: dict[str, Any] | None = None,
            headers: dict[str, Any] | None = None,
        ) -> _Resp:
            del headers
            bus_journal.append(("patch", path, json))
            return _Resp(200, {"status": "closed", "dispatch_links": []})

    return _Client


@pytest.mark.asyncio
async def test_pre_admission_refusal_integration_closes_and_stamps_failed() -> None:
    """AC3: real rollback/close paths; GIW 409 pre_admission; bus terminate + close."""
    giw_body = {
        "code": "CURSOR_SOURCE_REF_IN_FLIGHT",
        "message": "source ref in flight",
        "data": {"validation_stage": "pre_admission"},
    }
    bus_journal: list[tuple[str, str, dict[str, Any] | None]] = []
    client_cls = _routing_bus_and_giw_client(
        giw_status=409, giw_body=giw_body, bus_journal=bus_journal
    )
    from .admission import FrontierEndpointError

    with (
        patch.dict("os.environ", {"ALLOW_UNSET_AGENT_BUS_TOKEN": "1"}, clear=False),
        patch(
            "systems.frontier_consult.cursor_sdk_worker_dispatch.make_async_client",
            client_cls,
        ),
        patch(
            "systems.frontier_consult.handoff.make_async_client",
            client_cls,
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        ),
        pytest.raises(FrontierEndpointError),
    ):
        await _finish_prepared_dispatch(_minted_handle())

    paths = [entry[1] for entry in bus_journal]
    assert any("/dispatch-terminate" in p for p in paths)
    assert any(p.endswith("/close") for p in paths)
    terminate = next(j for j in bus_journal if "dispatch-terminate" in j[1])
    assert terminate[2]["terminal_status"] == "failed"
    assert terminate[2]["execution_id"] == "exec-pre"
    failed_turn = next(j for j in bus_journal if j[0] == "post" and j[1] == "/turns")
    assert failed_turn[2]["subject"].startswith("FAILED|")
    assert "CURSOR_SOURCE_REF_IN_FLIGHT" in failed_turn[2]["subject"]


@pytest.mark.asyncio
async def test_pre_admission_refusal_leaves_reused_thread_open() -> None:
    """Caller-supplied reuse_thread must not PATCH close after FAILED|."""
    giw_body = {
        "code": "CURSOR_SOURCE_REF_IN_FLIGHT",
        "message": "source ref in flight",
        "data": {"validation_stage": "pre_admission"},
    }
    bus_journal: list[tuple[str, str, dict[str, Any] | None]] = []
    client_cls = _routing_bus_and_giw_client(
        giw_status=409, giw_body=giw_body, bus_journal=bus_journal
    )
    from .admission import FrontierEndpointError

    with (
        patch.dict("os.environ", {"ALLOW_UNSET_AGENT_BUS_TOKEN": "1"}, clear=False),
        patch(
            "systems.frontier_consult.cursor_sdk_worker_dispatch.make_async_client",
            client_cls,
        ),
        patch(
            "systems.frontier_consult.handoff.make_async_client",
            client_cls,
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        ),
        pytest.raises(FrontierEndpointError),
    ):
        await _finish_prepared_dispatch(
            _minted_handle(thread_id="900", worker_thread_reused=True)
        )

    assert not any(entry[0] == "patch" for entry in bus_journal)
