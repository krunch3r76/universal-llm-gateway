"""Agent-facing resume bundle projection — one canonical copy per repeated field."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def _sha256_obj(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _dedupe_notices(mission: dict[str, Any], envelope: dict[str, Any]) -> None:
    house = mission.get("house_unread")
    if not isinstance(house, list):
        return
    tape = envelope.get("tape_verbal")
    if not isinstance(tape, list):
        return
    tape_ids = {
        f"{row.get('turn_number')}:{row.get('subject')}"
        for row in tape
        if isinstance(row, dict)
    }
    if not tape_ids:
        return
    filtered = []
    for row in house:
        if not isinstance(row, dict):
            filtered.append(row)
            continue
        ident = f"{row.get('turn')}:{row.get('subject')}"
        if ident in tape_ids:
            continue
        filtered.append(row)
    mission["house_unread"] = filtered


def project_resume_bundle_agent(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return a compact agent-facing view; canonical stored bundle is unchanged."""
    if bundle.get("error"):
        return bundle
    full_sha = _sha256_obj(bundle)
    out = copy.deepcopy(bundle)
    mission = out.get("mission")
    if not isinstance(mission, dict):
        mission = {}
    preamble_top = out.pop("preamble", None)
    preamble_mission = mission.pop("preamble", None)
    preamble = preamble_top or preamble_mission
    if preamble:
        out["preamble"] = preamble
        mission.pop("next_action", None)
    skills = mission.get("skills_to_use") or out.get("skills_to_use")
    if skills:
        out["skills_to_use"] = skills
        mission.pop("skills_to_use", None)
    rules = mission.get("standing_rules")
    if rules:
        mission["standing_rules"] = rules
    handoff = mission.get("handoff")
    if isinstance(handoff, dict):
        steps = handoff.get("steps") or handoff.get("handoff_steps")
        if isinstance(steps, list):
            seen_na: set[str] = set()
            for step in steps:
                if not isinstance(step, dict):
                    continue
                detail = str(step.get("detail") or step.get("action") or "")
                if "next_action" in detail.lower():
                    if "next_action" in seen_na:
                        step["detail"] = "see bundle.preamble.next_action"
                    else:
                        seen_na.add("next_action")
    tail = out.get("tail")
    if isinstance(tail, dict) and preamble and tail.get("next_action") == preamble.get(
        "next_action"
    ):
        out.pop("tail", None)
    envelope = out.get("resume_envelope")
    if isinstance(envelope, dict) and isinstance(mission, dict):
        _dedupe_notices(mission, envelope)
    out["mission"] = mission
    out["agent_projection"] = {
        "source": "resume_agent_projection",
        "full_bundle_sha256": full_sha,
        "provenance": "stored bundle unchanged; GET fence_id for canonical bytes",
    }
    return out


__all__ = ["project_resume_bundle_agent"]
