"""Hermetic proof: loop stays live during blocked send; GET + single-flight pour."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.resume_fence import encode_resume_bundle
from agent_bus_store.resume_fence_delivery import (
    _IN_FLIGHT,
    deliver_resume_bundle,
    pour_in_flight,
    pour_key,
    read_stored_bundle,
)
from agent_bus_store.routes.threads.resume_fence import get_thread_resume_bundle
from agent_bus_store.routes.threads.send import send_route
from agent_bus_store.turns_models import TurnSendCreate

pytestmark = pytest.mark.offline

_TIP_BODY = "agent-bus:10223 only checkpoint body"
_CARD = """
## Pools
| pool | executor | status | must_load | must_read | closeout | forbidden |
| --- | --- | --- | --- | --- | --- | --- |
| orchestrator | cursor | open | ulg-for-llms | tip CP | agent-bus:10303 | posts on 10223 |
"""
_ENVELOPE = {
    "scope": "last_session",
    "seal_status": "sealed",
    "tape_verbal": [],
    "checkpoint_highlight": "highlight",
    "consolidate_summary_row": "row",
    "summary_row_source": "l3",
    "summary_row_as_of_turn": 507,
}


@pytest.fixture()
def root_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    create_thread(thread_id="10223", slug="continuity", tags=["role:root"])
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="cursor",
        subject="CHECKPOINT",
        body=_TIP_BODY,
        status="open",
    )
    create_thread(thread_id="10400", slug="child-lane", tags=[])
    create_turn(
        thread_id="10400",
        from_agent="cursor",
        to_agent="cursor",
        subject="work",
        body="child turn",
        status="open",
    )
    _IN_FLIGHT.clear()
    yield "10223"
    _IN_FLIGHT.clear()


class _PourPatches:
    def __enter__(self):
        self._stack = ExitStack()
        self._stack.enter_context(
            patch(
                "agent_bus_store.resume_fence.build_resume_envelope",
                return_value=_ENVELOPE,
            )
        )
        self._stack.enter_context(
            patch(
                "agent_bus_store.resume_fence.load_continuity_card",
                return_value=_CARD,
            )
        )
        self._stack.enter_context(
            patch(
                "agent_bus_store.resume_fence.resolve_code_version",
                return_value="abc123",
            )
        )
        self._stack.enter_context(
            patch(
                "agent_bus_store.resume_fence_delivery.load_continuity_card",
                return_value=_CARD,
            )
        )
        self._stack.enter_context(
            patch(
                "agent_bus_store.resume_fence_delivery.resolve_code_version",
                return_value="abc123",
            )
        )
        return self._stack

    def __exit__(self, *args):
        self._stack.close()


def pour_patches():
    return _PourPatches()


def test_loop_runs_and_get_serves_while_continue_send_blocked(root_env) -> None:
    root = root_env

    async def _run() -> None:
        with pour_patches():
            await deliver_resume_bundle(
                root, transcript_id="tab-a", source="test", pool=None
            )
        row = read_stored_bundle(root, "tab-a")
        assert row is not None

        entered = threading.Event()
        release = threading.Event()
        real_create_turn = __import__(
            "agent_bus_store.db", fromlist=["create_turn"]
        ).create_turn

        def blocking_create_turn(*args, **kwargs):
            entered.set()
            release.wait(10)
            return real_create_turn(*args, **kwargs)

        with patch(
            "agent_bus_store.routes.threads.send.create_turn",
            side_effect=blocking_create_turn,
        ):
            payload = TurnSendCreate.model_validate(
                {
                    "thread": "10400",
                    "from": "cursor",
                    "to": "cursor",
                    "subject": "continue work",
                    "body": "more on child lane",
                }
            )
            send_task = asyncio.create_task(send_route(payload))
            ok = await asyncio.to_thread(entered.wait, 5)
            assert ok is True

            ran = await asyncio.wait_for(asyncio.sleep(0, result="ran"), 1)
            assert ran == "ran"

            resp = await asyncio.wait_for(
                get_thread_resume_bundle(root, transcript_id="tab-a"), 2
            )
            assert (
                hashlib.sha256(encode_resume_bundle(resp["bundle"])).hexdigest()
                == row.sha256
            )
            assert resp["fence_id"] == row.fence_id
            assert not send_task.done()

            release.set()
            created = await send_task
            assert created.send_path == "continue"

    asyncio.run(_run())


def test_overlapping_pours_same_key_one_fence_one_row(root_env) -> None:
    root = root_env
    call_count = {"n": 0}
    entered = threading.Event()
    release = threading.Event()

    real_assemble = __import__(
        "agent_bus_store.resume_fence", fromlist=["assemble_resume_fence"]
    ).assemble_resume_fence

    def counting_assemble(*args, **kwargs):
        call_count["n"] += 1
        entered.set()
        release.wait(10)
        return real_assemble(*args, **kwargs)

    async def _run() -> None:
        key = pour_key(root, "tab-b")
        with pour_patches(), patch(
            "agent_bus_store.resume_fence_delivery.assemble_resume_fence",
            side_effect=counting_assemble,
        ):
            t1 = asyncio.create_task(
                deliver_resume_bundle(
                    root, transcript_id="tab-b", source="test", pool=None
                )
            )
            for _ in range(200):
                if pour_in_flight(key) and entered.is_set():
                    break
                await asyncio.sleep(0.01)
            t2 = asyncio.create_task(
                deliver_resume_bundle(
                    root, transcript_id="tab-b", source="test", pool=None
                )
            )
            await asyncio.sleep(0.05)
            release.set()
            a, b = await asyncio.gather(t1, t2)

        assert call_count["n"] == 1
        assert a["fence"]["fence_id"] == b["fence"]["fence_id"]
        fid = a["fence"]["fence_id"]

        with connect() as conn:
            poured = conn.execute(
                """
                SELECT COUNT(DISTINCT fence_id) FROM resume_fence_events
                WHERE root_thread = ? AND transcript_id = 'tab-b' AND event = 'poured'
                """,
                (root,),
            ).fetchone()[0]
            bundles = conn.execute(
                "SELECT COUNT(*) FROM resume_fence_bundles"
            ).fetchone()[0]
            journal = conn.execute(
                """
                SELECT payload_json FROM resume_fence_events
                WHERE fence_id = ? AND event = 'poured'
                """,
                (fid,),
            ).fetchone()
            row_sha = conn.execute(
                "SELECT sha256 FROM resume_fence_bundles WHERE fence_id = ?",
                (fid,),
            ).fetchone()[0]

        assert poured == 1
        assert bundles == 1
        assert json.loads(journal[0])["bundle_sha256"] == row_sha

        armed_before = 0
        with connect() as conn:
            armed_before = conn.execute(
                """
                SELECT COUNT(*) FROM resume_fence_events
                WHERE root_thread = ? AND transcript_id = 'tab-b' AND event = 'armed'
                """,
                (root,),
            ).fetchone()[0]

        with pour_patches():
            c = await deliver_resume_bundle(
                root, transcript_id="tab-b", source="test", pool=None
            )
        assert c["fence"]["fence_id"] == fid
        assert call_count["n"] == 1

        with connect() as conn:
            armed_after = conn.execute(
                """
                SELECT COUNT(*) FROM resume_fence_events
                WHERE root_thread = ? AND transcript_id = 'tab-b' AND event = 'armed'
                """,
                (root,),
            ).fetchone()[0]
        assert armed_after == armed_before

    asyncio.run(_run())
