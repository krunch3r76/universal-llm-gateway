#!/usr/bin/env python3
"""Roadmap 1.2 — retire matter agent_skill rows migrated in waves B–C.

Spec: tasks/specs/skill-guidance-case-discovery.md §1.4
Prerequisite: waves A–C complete (has_playbook + playbook documents).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "libs")

from cortex_store.dispatch_ops import execute_op

CORTEX_ROOT = Path(os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files"))
SKILLS = CORTEX_ROOT / "agent-skills"
ARCHIVE = CORTEX_ROOT / "trash/agent-skills/archived"
REPO = Path(__file__).resolve().parent.parent.parent
AGENT = "cursor"
SESSION = "cursor-retire-matter-skills-1-2"

# slug → (document entity, case id, body path)
RETIREMENTS: dict[str, tuple[str, str, str]] = {
    "chase-escrow-discipline": (
        "document:chase-escrow-discipline",
        "case:chase-escrow-flintridge-2026",
        "notes/finance/chase-escrow/discipline.md",
    ),
    "chase-escrow-statement-ingestion": (
        "document:chase-escrow-discipline",
        "case:chase-escrow-flintridge-2026",
        "notes/finance/chase-escrow/discipline.md",
    ),
    "boe19p-appeal-discipline": (
        "document:boe19p-appeal-discipline",
        "case:boe19p-flintridge-appeal-2026",
        "notes/legal/property-tax/boe19p-discipline.md",
    ),
    "flintridge-case-navigation": (
        "document:flintridge-case-navigation",
        "case:chase-escrow-flintridge-2026",
        "notes/finance/flintridge/navigation.md",
    ),
    "hei-application-discipline": (
        "document:hei-discipline",
        "case:hei-flintridge-2026",
        "notes/finance/hei/discipline.md",
    ),
}


def _op(tool: str, payload: dict) -> dict:
    result = execute_op(tool, payload)
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(f"{tool} failed: {result}")
    return result if isinstance(result, dict) else {}


def _lifecycle(slug: str) -> str | None:
    row = execute_op(
        "entity_get", {"entity_id": f"agent_skill:{slug}", "intent": "card"}
    )
    if isinstance(row, dict) and row.get("error"):
        return None
    return str(row.get("lifecycle") or "")


def _retire_slug(slug: str, *, dry_run: bool) -> None:
    doc_id, case_id, body_path = RETIREMENTS[slug]
    entity_id = f"agent_skill:{slug}"
    if _lifecycle(slug) == "retired":
        print(f"  skip {slug} (already retired)")
        return

    claim = (
        f"RETIRED: superseded by {doc_id} on {case_id} (has_playbook). "
        f"Body relocated to cortex://{body_path}."
    )
    if dry_run:
        print(f"  would retire {entity_id} → {doc_id}")
        return

    _op(
        "assert",
        {
            "entity_id": entity_id,
            "claim": claim,
            "confidence": "confirmed",
            "evidence": f"Roadmap 1.2 retirement; playbook at cortex://{body_path}",
            "evidence_uris": [f"cortex://{body_path}", doc_id],
            "derivation_type": "inference",
            "seeded_by": AGENT,
            "session_id": SESSION,
            "agent": AGENT,
        },
    )
    _op(
        "entity_update",
        {
            "entity_id": entity_id,
            "lifecycle": "retired",
            "attributes": {
                "guidance_class": "retired",
                "export_surfaces": [],
            },
        },
    )

    src = SKILLS / f"{slug}.md"
    if src.is_file():
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        dest = ARCHIVE / f"{slug}.md"
        if dest.exists():
            dest.unlink()
        shutil.move(str(src), str(dest))
        print(f"  archived {src.name}")

    cursor_dir = REPO / ".cursor" / "skills" / slug
    if cursor_dir.is_dir():
        shutil.rmtree(cursor_dir)
        print(f"  removed {cursor_dir.relative_to(REPO)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=== Roadmap 1.2: retire matter skills (waves B–C) ===")
    for slug in RETIREMENTS:
        _retire_slug(slug, dry_run=args.dry_run)

    if not args.dry_run:
        _op(
            "assert",
            {
                "entity_id": "todo:skill-guidance-surface-migration",
                "claim": (
                    "Roadmap 1.2 complete for waves B–C slugs: five matter "
                    "agent_skill rows retired; bodies archived under "
                    "trash/agent-skills/archived/; discovery via has_playbook."
                ),
                "confidence": "confirmed",
                "evidence": "scripts/cortex/retire_matter_skills_wave_bc.py",
                "evidence_uris": ["agent-bus:4049"],
                "derivation_type": "inference",
                "seeded_by": AGENT,
                "session_id": SESSION,
                "agent": AGENT,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
