"""Agent-facing resume bundle projection — dedupe only demonstrated equivalence."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def _sha256_obj(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _same_text(a: Any, b: Any) -> bool:
    return str(a or "").strip() == str(b or "").strip() and bool(str(a or "").strip())


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
    if preamble_top and preamble_mission and preamble_top != preamble_mission:
        out["preamble"] = preamble_top
        mission["preamble"] = preamble_mission
    elif preamble_top or preamble_mission:
        out["preamble"] = preamble_top or preamble_mission

    preamble_na = (out.get("preamble") or {}).get("next_action")
    mission_na = mission.get("next_action")
    if _same_text(preamble_na, mission_na):
        mission.pop("next_action", None)
        mission["next_action_source"] = "preamble"
    elif preamble_na and not mission_na:
        mission["next_action_source"] = "preamble"
    elif mission_na:
        mission["next_action_source"] = "mission"

    skills = mission.get("skills_to_use") or out.get("skills_to_use")
    if skills:
        out["skills_to_use"] = skills
        mission.pop("skills_to_use", None)

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
    if isinstance(tail, dict) and _same_text(tail.get("next_action"), preamble_na):
        tail = dict(tail)
        tail.pop("next_action", None)
        if tail:
            out["tail"] = tail
        else:
            out.pop("tail", None)

    out["mission"] = mission
    out["agent_projection"] = {
        "source": "resume_agent_projection",
        "full_bundle_sha256": full_sha,
        "canonical_retrieval": "GET /threads/{root}/resume-fence?transcript_id=… (bundle_canonical_sha256)",
    }
    return out


__all__ = ["project_resume_bundle_agent"]
