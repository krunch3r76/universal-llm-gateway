"""Admit a grokbot checkpoint: options, path escape, idempotent replay."""

from __future__ import annotations

import asyncio
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import refuse, rel_path_ok, step_output_json

_MODES = frozenset({"light", "seal", "verify_resume"})
_PATH_KEYS = (
    "card_uri",
    "envelope_ref",
    "snapshot_uri",
)
HOUSE_LOCKS: dict[str, asyncio.Lock] = {}


def house_lock(house: str) -> asyncio.Lock:
    lock = HOUSE_LOCKS.get(house)
    if lock is None:
        lock = asyncio.Lock()
        HOUSE_LOCKS[house] = lock
    return lock


def fact_subject(options: dict[str, Any]) -> str:
    house = str(options.get("house") or "")
    n = options.get("n")
    default = f"FACT|house-cp|{house}|{n}"
    return str(options.get("fact_subject") or default)


def _paths(options: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in _PATH_KEYS:
        value = options.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    return found


async def run_admit(options: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    mode = str(options.get("mode") or "")
    if mode not in _MODES:
        return refuse("admit.mode", f"mode {mode!r} not in light|seal|verify_resume")
    house = str(options.get("house") or "").strip()
    if not house:
        return refuse("admit.house", "house required")
    n = options.get("n")
    if not isinstance(n, int) or n < 1:
        return refuse("admit.n", "n must be an int >= 1")
    for path in _paths(options):
        if not rel_path_ok(path):
            return refuse("admit.path_escape", f"refused path {path}")
    residue = options.get("residue")
    if isinstance(residue, str) and len(residue) > 800:
        return refuse("admit.residue", "residue exceeds 800 chars")
    prior = step_output_json(outputs, "prior")
    if prior.get("replayed"):
        return prior
    card_rows = options.get("_card_rows")
    fact_exists = bool(options.get("_fact_exists"))
    if (
        isinstance(card_rows, list)
        and card_rows
        and _last_cp(card_rows) == n
        and fact_exists
    ):
        return {
            "refused": None,
            "replayed": True,
            "house": house,
            "n": n,
            "mode": mode,
            "completed_steps": ["admit"],
        }
    return {
        "refused": None,
        "replayed": False,
        "house": house,
        "n": n,
        "mode": mode,
    }


def _last_cp(rows: list[Any]) -> int | None:
    last = rows[-1]
    if isinstance(last, dict) and isinstance(last.get("n"), int):
        return last["n"]
    return None


class GrokbotCheckpointAdmitHandler(BaseHandler):
    step_type = "grokbot_checkpoint_admit_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        async with house_lock(str(options.get("house") or "")):
            payload = await run_admit(options, getattr(context, "outputs", {}) or {})
        return StepOutput(raw="", json=payload)
