#!/usr/bin/env python3
"""Reproducible UTF-8 measurement for friction:42277 housekeeping (neutral fixture)."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "libs/bus_watch/fixtures/housekeeping_context_fixture.json"

sys.path.insert(0, str(REPO / "libs"))

from agent_bus_store.resume_agent_projection import (  # noqa: E402
    project_resume_bundle_agent,
)
from agent_bus_store.resume_fence import (  # noqa: E402
    encode_resume_bundle,
    resume_bundle_sha256,
)
from bus_watch.digest_agent_projection import (  # noqa: E402
    project_liaison_digest_agent,
    serialize_agent_digest,
)
from bus_watch.hop_skill_sections import (  # noqa: E402
    build_skill_section_plan,
    format_skill_load_block,
    measure_operation_skill_utf8,
)


def _skill_full_files_utf8() -> int:
    total = 0
    base = REPO / "cursor-plugins/ulg-ecosystem/skills"
    for slug in ("ide-continuity", "checkpoint-discipline", "reasoning-posture", "lean-dispatch"):
        path = base / slug / "SKILL.md"
        if path.is_file():
            total += len(path.read_text(encoding="utf-8").encode("utf-8"))
    return total


def _toc_overhead_utf8() -> int:
    plan = build_skill_section_plan("house_resume")
    return sum(len(row.fs_call.encode("utf-8")) for row in plan)


def main() -> int:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    digest = data["digest"]
    bundle = data["resume_bundle"]

    digest_raw = len(
        json.dumps(digest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    proj_digest = project_liaison_digest_agent(digest)
    digest_proj = len(serialize_agent_digest(proj_digest))

    resume_raw = len(encode_resume_bundle(bundle))
    resume_canonical_sha = resume_bundle_sha256(bundle)
    proj_bundle = project_resume_bundle_agent(bundle)
    resume_proj = len(encode_resume_bundle(proj_bundle))

    section_bodies, plan_overhead = measure_operation_skill_utf8("house_resume")
    compact_plan = len(format_skill_load_block("house_resume", detail="compact").encode("utf-8"))
    full_files = _skill_full_files_utf8()

    combined_before = digest_raw + resume_raw + full_files
    combined_after = digest_proj + resume_proj + section_bodies + plan_overhead + compact_plan

    report = {
        "fixture": str(FIXTURE.relative_to(REPO)),
        "digest_raw_utf8": digest_raw,
        "digest_projected_wire_utf8": digest_proj,
        "resume_raw_utf8": resume_raw,
        "resume_canonical_sha256": resume_canonical_sha,
        "resume_projected_utf8": resume_proj,
        "skill_full_files_utf8": full_files,
        "skill_section_bodies_utf8": section_bodies,
        "skill_plan_fs_call_overhead_utf8": plan_overhead,
        "skill_toc_plan_overhead_utf8": _toc_overhead_utf8(),
        "successor_wake_compact_plan_utf8": compact_plan,
        "combined_before_utf8": combined_before,
        "combined_after_utf8": combined_after,
        "combined_reduction_pct": round(100 * (1 - combined_after / combined_before), 2),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
