"""Concurrency and ordering tests for grok spawn idempotency slots."""

from __future__ import annotations

import concurrent.futures
import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cursor_bridge.grok_agent_ops import spawn_grok_agent_async
from cursor_bridge.grok_spawn_idempotency import (
    begin,
    clear_spawn_cache_for_tests,
    mark_steer,
    release,
)

pytestmark = pytest.mark.offline

_CLIENT_NONCE_RE = re.compile(r'"clientNonce":\s*"([^"]+)"')


def _canonical_uuid(nonce: str) -> None:
    """Break: Grok Bot rejects non-UUID clientNonce on createAgent."""
    assert str(uuid.UUID(nonce)) == nonce


@pytest.mark.parametrize(
    "key",
    [
        "plain-key",
        uuid.uuid4().hex,
        str(uuid.uuid4()),
    ],
)
def test_begin_create_mints_canonical_uuid_nonce(key: str) -> None:
    clear_spawn_cache_for_tests()
    digest = "d" * 16
    out = begin(key, digest)
    assert out["action"] == "create"
    nonce = str(out["nonce"])
    _canonical_uuid(nonce)
    assert nonce != key or key == str(uuid.UUID(key))


def test_begin_create_nonce_independent_of_key() -> None:
    clear_spawn_cache_for_tests()
    digest = "d" * 16
    a = begin("key-a", digest)
    b = begin("key-b", digest)
    assert a["action"] == "create" and b["action"] == "create"
    assert str(a["nonce"]) != str(b["nonce"])


def test_begin_adopt_after_create_unknown_reuses_slot_nonce() -> None:
    clear_spawn_cache_for_tests()
    key = "persist-nonce"
    digest = "d" * 16
    first = begin(key, digest)
    assert first["action"] == "create"
    first_nonce = str(first["nonce"])
    _canonical_uuid(first_nonce)
    token = str(first["token"])
    release(key, token, outcome="create_unknown")
    adopt = begin(key, digest)
    assert adopt["action"] == "adopt"
    assert str(adopt["nonce"]) == first_nonce


def _prepare_create_unknown(key: str, digest: str) -> str:
    first = begin(key, digest)
    assert first["action"] == "create"
    token = str(first["token"])
    release(key, token, outcome="create_unknown")
    return token


def test_concurrent_adopt_on_create_unknown_one_adopt_one_in_flight() -> None:
    """Two retries on create_unknown: only one adopt; peer gets idempotency_in_flight."""
    clear_spawn_cache_for_tests()
    key = "concurrent-adopt-key"
    digest = "d" * 16
    _prepare_create_unknown(key, digest)

    def _claim() -> dict:
        return begin(key, digest)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fut_a, fut_b = pool.submit(_claim), pool.submit(_claim)
        a, b = fut_a.result(), fut_b.result()

    actions = sorted([str(a.get("action")), str(b.get("action"))])
    assert actions == ["adopt", "in_flight"]
    codes = [r.get("code") for r in (a, b) if r.get("action") == "in_flight"]
    assert codes == ["idempotency_in_flight"]

    winner = a if a.get("action") == "adopt" else b
    token = str(winner["token"])
    mark_steer(key, token, "attempting")
    mark_steer(key, token, "sent")
    release(key, token, outcome="created")
    retry = begin(key, digest)
    assert retry["action"] != "adopt"


@pytest.mark.asyncio
async def test_spawn_passes_minted_uuid_client_nonce_to_js_spawn() -> None:
    """Break: fallback to idempotency key would send a non-UUID clientNonce."""
    clear_spawn_cache_for_tests()
    idem = "plain-idem-key"
    seen_nonces: list[str] = []

    async def _eval(_display: object, expr: str, **_kw: Any) -> dict[str, Any]:
        if "createAgent" in expr:
            m = _CLIENT_NONCE_RE.search(expr)
            assert m is not None
            seen_nonces.append(m.group(1))
            return {"ok": True, "created": True, "agent_id": "spawned-uuid"}
        if "setAgentHiddenFromSidebar" in expr:
            return {"ok": True, "hidden": True, "code": "hidden"}
        if "rowMarksActive" in expr:
            return {
                "ok": True,
                "row_agent_id": "spawned-uuid",
                "currentAgentId": "spawned-uuid",
            }
        return {"ok": True, "currentAgentId": "spawned-uuid"}

    with (
        patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval),
        patch(
            "interlocutor.display_adapter.steer_display",
            new_callable=AsyncMock,
            return_value={"ok": True, "delivered": True, "pasted": True},
        ),
    ):
        out = await spawn_grok_agent_async(
            "9", "bot-nonce", idempotency_key=idem, text="hello"
        )
    assert out["ok"] is True
    assert len(seen_nonces) == 1
    nonce = seen_nonces[0]
    _canonical_uuid(nonce)
    assert nonce != idem
    receipt_nonce = str(out.get("clientNonce") or "")
    assert receipt_nonce == nonce
