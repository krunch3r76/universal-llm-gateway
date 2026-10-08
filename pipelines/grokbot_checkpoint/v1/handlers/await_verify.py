"""Poll the verify lane for IN-SYNC or DRIFT. Never reads the card."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get, step_output_json


def wait_transport_backoff_s(round_index: int) -> float:
    return float(min(2**round_index, 10))


def _classify(body: str, n: int, house: str) -> str | None:
    house_re = re.escape(house)
    if re.search(rf"IN-SYNC\|{house_re}\|{n}\b", body):
        return "in_sync"
    if re.search(rf"DRIFT\|{house_re}\|{n}\b", body):
        return "drift"
    return None


async def run_await_verify(
    options: dict[str, Any],
    fact: dict[str, Any],
    *,
    get: Any = None,
    sleep: Any = None,
    now: Any = None,
) -> dict[str, Any]:
    get = get or bus_get
    sleep = sleep or asyncio.sleep
    now = now or time.monotonic
    budget = float(options.get("wait_verify_seconds") or 900)
    budget = min(budget, 1200.0)
    deadline = now() + budget
    after = int(fact.get("turn") or 0)
    lane = str(options.get("verify_lane") or "")
    verifier = str(options.get("verifier") or "")
    n = int(options["n"])
    house = str(options.get("house") or "")
    round_index = 0
    while now() < deadline:
        try:
            payload, status = await get(
                f"/threads/{lane}/turns",
                params={"after_turn": after},
            )
        except Exception:
            await sleep(wait_transport_backoff_s(round_index))
            round_index += 1
            continue
        if status >= 500:
            await sleep(wait_transport_backoff_s(round_index))
            round_index += 1
            continue
        turns = payload.get("turns") if isinstance(payload, dict) else []
        if isinstance(turns, list):
            for turn in turns:
                if str(turn.get("from") or "") != verifier:
                    continue
                state = _classify(str(turn.get("body") or ""), n, house)
                if state:
                    return {
                        "refused": None,
                        "state": state,
                        "turn": turn.get("turn_number"),
                    }
        before = now()
        await sleep(min(5.0, max(deadline - before, 0.0)))
        round_index += 1
        if now() <= before:
            break
    return {"refused": None, "state": "pending", "turn": None}


class GrokbotCheckpointAwaitVerifyHandler(BaseHandler):
    step_type = "grokbot_checkpoint_await_verify_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        fact = step_output_json(getattr(context, "outputs", {}) or {}, "fact")
        payload = await run_await_verify(options, fact)
        return StepOutput(raw="", json=payload)
