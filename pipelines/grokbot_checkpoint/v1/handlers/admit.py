"""Admit a grokbot checkpoint: options, path escape, idempotent replay."""

from __future__ import annotations

import uuid
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get, fs_of, refuse, rel_path_ok, step_output_json
from ._handoff import normalize_handoff, validate_handoff
from .card import _last_row_n

_MODES = frozenset({"light", "seal", "verify_resume", "harvest_seal"})
_PATH_KEYS = (
    "card_uri",
    "transcribed_ref",
    "raw_ref",
    "l0_ref",
    "snapshot_uri",
)


def _checkpoint_prefix(agent_id: str) -> str:
    return f"notes/system/checkpoints/grok/{agent_id.strip()}/"


async def _max_n(fs: Any, agent_id: str) -> int:
    import re

    names = await fs.list(_checkpoint_prefix(agent_id).rstrip("/"))
    best = 0
    if not names:
        return 0
    for name in names:
        base = name.rsplit("/", 1)[-1]
        match = re.fullmatch(r"(\d+)\.(?:raw|messages)\.json", base)
        if match:
            best = max(best, int(match.group(1)))
    return best


async def _next_n(fs: Any, agent_id: str) -> int:
    return await _max_n(fs, agent_id) + 1


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
        return refuse(
            "admit.mode",
            f"mode {mode!r} not in light|seal|verify_resume|harvest_seal",
        )
    house = str(options.get("house") or "").strip()
    houseless = options.get("houseless") is True or house.lower() == "none"
    if houseless and mode != "harvest_seal":
        return refuse("admit.houseless_mode", "houseless is only valid in harvest_seal")
    if houseless and house and house.lower() != "none":
        return refuse("admit.houseless_conflict", "house and houseless both set")
    if not houseless and not house:
        return refuse("admit.house", "house required")
    if houseless:
        house = ""
        options["house"] = ""
    n = options.get("n")
    if n is None and houseless and fs is not None:
        n = await _next_n(fs, str(options.get("agent_id") or ""))
        options["n"] = n
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        return refuse("admit.n", "n must be an int >= 1")
    if houseless and fs is not None:
        have = await _max_n(fs, str(options.get("agent_id") or ""))
        if n > have + 1:
            return refuse("admit.n", f"n {n} skips checkpoint {have + 1}")
    for path in _paths(options):
        if not rel_path_ok(path):
            return refuse("admit.path_escape", f"refused path {path}")
    if (
        mode in ("seal", "harvest_seal")
        and not str(options.get("agent_id") or "").strip()
    ):
        return refuse("admit.agent_id", "agent_id required in seal mode")
    if mode == "harvest_seal" and not str(options.get("l0_ref") or "").strip():
        return refuse("source.no_pour", "harvest_seal requires l0_ref")
    root_thread = str(options.get("root_thread") or "").strip()
    agent_id = str(options.get("agent_id") or "").strip()
    if mode in ("seal", "harvest_seal") and root_thread and agent_id:
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
    normalized_fields: list[str] = []
    if "handoff" in options and options.get("handoff") is not None:
        if mode not in ("seal", "harvest_seal"):
            return refuse("admit.handoff_mode", "handoff is only admitted in seal mode")
        raw_letter = options.get("handoff")
        normalized, normalized_fields = (
            normalize_handoff(raw_letter)
            if isinstance(raw_letter, dict)
            else (raw_letter, [])
        )
        if isinstance(raw_letter, dict):
            options["handoff"] = normalized
        defect = validate_handoff(normalized)
        if defect is not None:
            return refuse(defect[0], defect[1])
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
        listing, status = await getter("/turns", params={"thread": lane})
        turns = listing.get("turns") if isinstance(listing, dict) else []
        subject = fact_subject(options)
        if status < 400 and isinstance(turns, list):
            fact_exists = any(
                t.get("subject") == subject and not t.get("superseded") for t in turns
            )
    advisory = (
        {"handoff.normalized": normalized_fields}
        if "handoff" in options and options.get("handoff") is not None
        else {}
    )
    if last_n == n and fact_exists:
        return {
            "refused": None,
            "replayed": True,
            "house": house,
            "n": n,
            "mode": mode,
            "agent_id": agent_id,
            "houseless": houseless,
            "completed_steps": ["admit"],
            **advisory,
        }
    return {
        "refused": None,
        "replayed": False,
        "house": house,
        "n": n,
        "mode": mode,
        "agent_id": agent_id,
        "houseless": houseless,
        **advisory,
    }


class GrokbotCheckpointAdmitHandler(BaseHandler):
    step_type = "grokbot_checkpoint_admit_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options_peek = dict(getattr(context, "options", {}) or {})
        house_peek = str(options_peek.get("house") or "").strip().lower()
        houseless_peek = options_peek.get("houseless") is True or house_peek == "none"
        if (
            not houseless_peek
            and not str(getattr(context, "dispatch_thread_id", "") or "").strip()
        ):
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
