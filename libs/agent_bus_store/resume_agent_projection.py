"""Agent-facing resume bundle projection — dedupe only demonstrated equivalence."""

from __future__ import annotations

import copy
from typing import Any

from agent_bus_store.resume_fence import resume_bundle_sha256


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
