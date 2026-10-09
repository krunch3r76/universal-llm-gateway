"""Admit a grokbot checkpoint: options, path escape, idempotent replay."""

from __future__ import annotations

import uuid
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get, fs_of, refuse, rel_path_ok, step_output_json
from .card import _last_row_n

_MODES = frozenset({"light", "seal", "verify_resume"})
_PATH_KEYS = (
    "card_uri",
    "transcribed_ref",
    "raw_ref",
    "snapshot_uri",
)


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


async def run_admit(
    options: dict[str, Any],
    outputs: dict[str, Any],
    *,
    fs: Any = None,
    get: Any = None,
) -> dict[str, Any]:
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
    if mode == "seal" and not str(options.get("agent_id") or "").strip():
        return refuse("admit.agent_id", "agent_id required in seal mode")
    root_thread = str(options.get("root_thread") or "").strip()
    agent_id = str(options.get("agent_id") or "").strip()
    if mode == "seal" and root_thread and agent_id:
        try:
            if str(uuid.UUID(agent_id)) != agent_id:
                return refuse(
                    "admit.agent_id_not_uuid",
                    "agent_id must be canonical lowercase uuid",
                )
        except ValueError:
            return refuse(
                "admit.agent_id_not_uuid",
                "agent_id must be canonical lowercase uuid",
            )
    residue = options.get("residue")
    if isinstance(residue, str) and len(residue) > 800:
        return refuse("admit.residue", "residue exceeds 800 chars")
    prior = step_output_json(outputs, "prior")
    if prior.get("replayed"):
        return prior
    fact_exists = False
    last_n = None
    if fs is not None and options.get("card_uri"):
        raw = await fs.read(str(options["card_uri"]))
        if raw:
            last_n = _last_row_n(raw.decode("utf-8"))
    getter = get or bus_get
    lane = str(options.get("verify_lane") or "")
    if lane:
        listing, status = await getter(f"/threads/{lane}/turns")
        turns = listing.get("turns") if isinstance(listing, dict) else []
        subject = fact_subject(options)
        if status < 400 and isinstance(turns, list):
            fact_exists = any(
                t.get("subject") == subject and not t.get("superseded") for t in turns
            )
    if last_n == n and fact_exists:
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


class GrokbotCheckpointAdmitHandler(BaseHandler):
    step_type = "grokbot_checkpoint_admit_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        if not str(getattr(context, "dispatch_thread_id", "") or "").strip():
            return StepOutput(
                raw="",
                json=refuse(
                    "admit.dispatch_thread_missing",
                    "concurrency key needs context.dispatch_thread_id",
                ),
            )
        options = dict(getattr(context, "options", {}) or {})
        payload = await run_admit(
            options,
            getattr(context, "outputs", {}) or {},
            fs=fs_of(context),
        )
        return StepOutput(raw="", json=payload)
