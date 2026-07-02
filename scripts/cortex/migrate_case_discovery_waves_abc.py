#!/usr/bin/env python3
"""Roadmap 2.0 — case discovery waves A–C (has_playbook + surface_forms + playbooks).

Spec: tasks/specs/skill-guidance-case-discovery.md (thread 4052).
Idempotent on re-run (skips existing entities/relationships/rows).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "libs")

from cortex_store.db import cortex_conn
from cortex_store.dispatch_ops import execute_op

CORTEX_ROOT = Path(os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files"))
SKILLS = CORTEX_ROOT / "agent-skills"
POLICY_URI = "cortex://notes/system/specs/skill-guidance-policy.md"
AGENT = "cursor"
SESSION = "cursor-case-discovery-waves-abc"

SURFACE_FORMS: dict[str, list[tuple[str, str]]] = {
    "case:chase-escrow-flintridge-2026": [
        ("Chase escrow", "trigger"),
        ("escrow shortage", "trigger"),
        ("escrow dispute", "trigger"),
        ("loan 8787", "identifier"),
        ("APN 424-20-043", "identifier"),
    ],
    "case:boe19p-flintridge-appeal-2026": [
        ("BOE-19-P", "trigger"),
        ("Prop 19 appeal", "trigger"),
        ("supplemental tax", "trigger"),
        ("APN 424-20-043", "identifier"),
        ("10059585", "identifier"),
        ("10059586", "identifier"),
        ("Greg Monteverde", "person"),
    ],
    "case:pge-gas-backbilling-dispute-2026": [
        ("PG&E back-billing", "trigger"),
        ("gas true-up", "trigger"),
        ("Rule 17.1", "trigger"),
        ("acct 84-9", "identifier"),
    ],
    "case:uber-driver-harassment-2026": [
        ("Uber harassment", "trigger"),
        ("phishing call", "trigger"),
        ("selfie verification", "trigger"),
        ("Shana", "person"),
    ],
    "case:rideshare-drivers-united-v-uber-2026": [
        ("RDU v Uber", "trigger"),
        ("Prop 22 class action", "trigger"),
        ("CGC26636126", "identifier"),
        ("Liss-Riordan", "person"),
    ],
}

PLAYBOOKS: list[dict[str, Any]] = [
    {
        "slug": "chase-escrow-discipline",
        "fold": ["chase-escrow-statement-ingestion"],
        "doc_id": "document:chase-escrow-discipline",
        "name": "Chase Escrow Dispute — matter playbook",
        "body_path": "notes/finance/chase-escrow/discipline.md",
        "cases": ["case:chase-escrow-flintridge-2026"],
        "migrated_from": "agent_skill:chase-escrow-discipline",
    },
    {
        "slug": "boe19p-appeal-discipline",
        "doc_id": "document:boe19p-appeal-discipline",
        "name": "BOE-19-P Appeal — matter playbook",
        "body_path": "notes/legal/property-tax/boe19p-discipline.md",
        "cases": ["case:boe19p-flintridge-appeal-2026"],
        "migrated_from": "agent_skill:boe19p-appeal-discipline",
    },
    {
        "slug": "flintridge-case-navigation",
        "doc_id": "document:flintridge-case-navigation",
        "name": "Flintridge case navigation — matter playbook",
        "body_path": "notes/finance/flintridge/navigation.md",
        "cases": [
            "case:chase-escrow-flintridge-2026",
            "case:boe19p-flintridge-appeal-2026",
            "case:pge-gas-backbilling-dispute-2026",
        ],
        "migrated_from": "agent_skill:flintridge-case-navigation",
    },
    {
        "slug": "hei-application-discipline",
        "doc_id": "document:hei-discipline",
        "name": "HEI Application — archived matter playbook",
        "body_path": "notes/finance/hei/discipline.md",
        "cases": ["case:hei-flintridge-2026"],
        "migrated_from": "agent_skill:hei-application-discipline",
        "wave": "c",
    },
]

ACTIVE_CASES = [
    ("case:chase-escrow-flintridge-2026", "Chase Mortgage Escrow Dispute", "active", "document:chase-escrow-discipline"),
    ("case:boe19p-flintridge-appeal-2026", "BOE-19-P Retroactive Supplemental Appeal", "active", "document:boe19p-appeal-discipline"),
    ("case:pge-gas-backbilling-dispute-2026", "PG&E Gas Back-Billing Dispute", "active", "document:flintridge-case-navigation"),
    ("case:uber-driver-harassment-2026", "Uber Driver Harassment", "open", "none — create on need"),
    ("case:rideshare-drivers-united-v-uber-2026", "RDU v Uber", "open", "none — create on need"),
]


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def _playbook_header(case_ids: list[str], migrated_from: str) -> str:
    cases = ", ".join(case_ids)
    return (
        f"# Matter playbook (migrated)\n\n"
        f"> **Cases:** {cases}\n"
        f"> **Migrated from:** {migrated_from}\n"
        f"> **Policy:** {POLICY_URI}\n\n"
    )


def _op(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = execute_op(tool, payload)
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(f"{tool} failed: {result}")
    return result if isinstance(result, dict) else {"result": result}


def _entity_exists(entity_id: str) -> bool:
    result = execute_op("entity_get", {"entity_id": entity_id, "intent": "card"})
    if isinstance(result, dict) and result.get("error"):
        if result.get("status_code") == 404:
            return False
        raise RuntimeError(f"entity_get failed: {result}")
    return True


def _rel_exists(source_id: str, target_id: str, type_id: str) -> bool:
    rows = _op(
        "relationships",
        {"entity_id": source_id, "type_id": type_id, "limit": 50},
    ).get("items", [])
    return any(r.get("target_id") == target_id for r in rows)


def wave_a(dry_run: bool) -> None:
    print("=== Wave A: infra ===")
    if dry_run:
        print("  (dry-run — skip writes)")
        return

    with cortex_conn() as conn:
        for case_id, rows in SURFACE_FORMS.items():
            for mention, mention_type in rows:
                exists = conn.execute(
                    "SELECT 1 FROM surface_forms WHERE entity_id = ? AND mention = ?",
                    (case_id, mention),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO surface_forms (mention, entity_id, mention_type) "
                    "VALUES (?, ?, ?)",
                    (mention, case_id, mention_type),
                )
        conn.commit()

    _write_active_cases(pending=True)
    _op(
        "assert",
        {
            "entity_id": "todo:skill-guidance-surface-migration",
            "claim": "Wave A discovery infra landed: has_playbook type registered, surface_forms seeded for 5 active cases, active-cases index note created.",
            "confidence": "confirmed",
            "evidence": "scripts/cortex/migrate_case_discovery_waves_abc.py wave A",
            "evidence_uris": [POLICY_URI, "agent-bus:4052"],
            "derivation_type": "inference",
            "seeded_by": AGENT,
            "session_id": SESSION,
            "agent": AGENT,
        },
    )
    print("  wave A complete")


def _write_active_cases(*, pending: bool) -> None:
    lines = [
        "# Active life/legal cases — discovery index",
        "",
        "Refresh in the same slice as any case lifecycle or has_playbook wiring change.",
        "",
        "| Case ID | Name | State | Playbook URI |",
        "|---|---|---|---|",
    ]
    for case_id, name, state, playbook in ACTIVE_CASES:
        uri = "pending wave B" if pending and playbook.startswith("document:") else playbook
        lines.append(f"| `{case_id}` | {name} | {state} | `{uri}` |")
    path = CORTEX_ROOT / "notes/system/indexes/active-cases.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _migrate_body(entry: dict[str, Any]) -> str:
    slug = entry["slug"]
    src = SKILLS / f"{slug}.md"
    body = _strip_frontmatter(src.read_text(encoding="utf-8"))
    header = _playbook_header(entry["cases"], entry["migrated_from"])
    for fold_slug in entry.get("fold", []):
        fold_src = SKILLS / f"{fold_slug}.md"
        fold_body = _strip_frontmatter(fold_src.read_text(encoding="utf-8"))
        body += f"\n\n## Statement ingestion\n\n{fold_body}\n"
    return header + body


def _banner_old_skill(slug: str, body_path: str) -> None:
    path = SKILLS / f"{slug}.md"
    text = path.read_text(encoding="utf-8")
    banner = (
        f"> **MIGRATED — edit `cortex://{body_path}` , not this file.** "
        f"Retirement in roadmap 1.2.\n\n"
    )
    if "MIGRATED — edit" in text:
        return
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            path.write_text(text[: end + 4] + "\n\n" + banner + text[end + 4 :], encoding="utf-8")
            return
    path.write_text(banner + text, encoding="utf-8")


def _wave_playbooks(wave: str, dry_run: bool) -> None:
    label = f"Wave {wave.upper()}"
    print(f"=== {label}: playbooks ===")
    for entry in PLAYBOOKS:
        if entry.get("wave", "b") != wave:
            continue
        doc_id = entry["doc_id"]
        body_path = entry["body_path"]
        body_text = _migrate_body(entry)
        if dry_run:
            print(f"  would migrate {entry['slug']} → {doc_id}")
            continue

        dest = CORTEX_ROOT / body_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body_text, encoding="utf-8")
        _banner_old_skill(entry["slug"], body_path)
        for fold_slug in entry.get("fold", []):
            _banner_old_skill(fold_slug, body_path)

        if not _entity_exists(doc_id):
            _op(
                "entity_create",
                {
                    "id": doc_id,
                    "type": "document",
                    "name": entry["name"],
                    "description": (
                        f"Matter playbook for {', '.join(entry['cases'])}. "
                        f"Migrated from {entry['migrated_from']}."
                    ),
                    "source_uri": f"cortex://{body_path}",
                    "attributes": {
                        "doc_class": "matter_playbook",
                        "migrated_from": entry["migrated_from"],
                    },
                },
            )

        for case_id in entry["cases"]:
            if not _rel_exists(case_id, doc_id, "has_playbook"):
                _op(
                    "relationship_create",
                    {
                        "source_id": case_id,
                        "target_id": doc_id,
                        "type_id": "has_playbook",
                        "evidence": "Roadmap 2.0 discovery layer",
                        "source_uri": POLICY_URI,
                        "session_id": SESSION,
                        "agent": AGENT,
                    },
                )

    if not dry_run and wave == "b":
        _supersede_chase_discipline_pointer()
        _write_active_cases(pending=False)
    print(f"  {label} complete")


def _supersede_chase_discipline_pointer() -> None:
    _op(
        "supersede",
        {
            "old_assertion_id": 12969,
            "entity_id": "case:chase-escrow-flintridge-2026",
            "claim": (
                "discipline_skill: governing matter playbook is "
                "document:chase-escrow-discipline (has_playbook on "
                "case:chase-escrow-flintridge-2026). Supersedes agent_skill pointer."
            ),
            "confidence": "confirmed",
            "evidence": "Roadmap 2.0 wave B migration",
            "evidence_uris": ["cortex://document:chase-escrow-discipline"],
            "derivation_type": "inference",
            "session_id": SESSION,
            "agent": AGENT,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wave",
        choices=("a", "b", "c", "all"),
        default="all",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    waves = ["a", "b", "c"] if args.wave == "all" else [args.wave]
    for wave in waves:
        if wave == "a":
            wave_a(args.dry_run)
        else:
            _wave_playbooks(wave, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
