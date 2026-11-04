"""Agent-facing resume bundle projection — dedupe only demonstrated equivalence."""

from __future__ import annotations

import copy
from typing import Any

from agent_bus_store.resume_fence import resume_bundle_sha256


def _same_text(a: Any, b: Any) -> bool:
    return str(a or "").strip() == str(b or "").strip() and bool(str(a or "").strip())


def project_resume_bundle_agent(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return a compact agent-facing view; canonical stored bundle is unchanged."""
    if bundle.get("error"):
        return bundle
    full_sha = resume_bundle_sha256(bundle)
    out = copy.deepcopy(bundle)
    mission = out.get("mission")
    if not isinstance(mission, dict):
        mission = {}

    skills = mission.pop("skills_to_use", None) or out.pop("skills_to_use", None)
    if skills:
        out["skills_to_use"] = skills

    preamble_na = (out.get("preamble") or {}).get("next_action")
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
        "canonical_retrieval": (
            "GET /threads/{root}/resume-fence?transcript_id=…&raw=1 "
            "(verify body hash == full_bundle_sha256)"
        ),
    }
    return out


__all__ = ["project_resume_bundle_agent"]
