#!/usr/bin/env python3
"""Tier 2 — write approve params to cortex and dispatch Opus project-ask."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PARAMS_REL = "notes/system/threads/5329-tier2-approve-params.md"
SEALED_URI = "cortex://notes/system/threads/5329-tier2-opus-batch-approve-sealed-ask.md"
ROOT = Path(__file__).resolve().parents[2]


def _cortex_files_root() -> Path:
    return Path(os.environ.get("CORTEX_FILES_ROOT", Path.home() / "mcp-data" / "files"))


def _write_params(
    *,
    ledger_id: int,
    staging_ids: list[int],
    entry_anchor: str,
    journal_uri: str,
    journal_entity_id: str,
    prior_sha256: str,
) -> Path:
    dest = _cortex_files_root() / PARAMS_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    ids_json = json.dumps(staging_ids)
    body = f"""# Tier 2 approve params (generated)

| Field | Value |
|---|---|
| **ledger_id** | {ledger_id} |
| **staging_ids** | `{ids_json}` |
| **entry_anchor** | `{entry_anchor}` |
| **journal_uri** | `{journal_uri}` |
| **journal_entity_id** | `{journal_entity_id or "unknown"}` |
| **prior_sha256** | `{prior_sha256 or ""}` |
| **generated_at** | {datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")} |

Sealed ask: {SEALED_URI}
"""
    dest.write_text(body, encoding="utf-8")
    return dest


def _load_json(path: str) -> dict:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Tier-2 Opus digest revision batch approve dispatch")
    parser.add_argument("--ledger-id", type=int)
    parser.add_argument("--staging-ids", help="Comma-separated staging row ids")
    parser.add_argument("--entry-anchor")
    parser.add_argument("--journal-uri")
    parser.add_argument("--journal-entity-id", default="")
    parser.add_argument("--prior-sha256", default="")
    parser.add_argument("--json", help="revision_staged JSON file or '-' for stdin")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ledger_id = args.ledger_id
    staging_ids: list[int] | None = None
    entry_anchor = args.entry_anchor
    journal_uri = args.journal_uri
    journal_entity_id = args.journal_entity_id
    prior_sha256 = args.prior_sha256

    if args.json:
        payload = _load_json(args.json)
        ledger_id = ledger_id or payload.get("ledger_id")
        if staging_ids is None:
            raw_ids = payload.get("emitted_ids") or payload.get("staging_ids") or []
            staging_ids = [int(x) for x in raw_ids]
        entry_anchor = entry_anchor or payload.get("entry_anchor")
        journal_uri = journal_uri or payload.get("journal_uri")
        journal_entity_id = journal_entity_id or payload.get("journal_entity_id", "")
        prior_sha256 = prior_sha256 or payload.get("prior_sha256", "")

    if args.staging_ids:
        staging_ids = [int(x.strip()) for x in args.staging_ids.split(",") if x.strip()]

    if ledger_id is None or not staging_ids or not entry_anchor or not journal_uri:
        parser.error("Need ledger_id, staging_ids, entry_anchor, journal_uri (flags or --json)")

    dest = _write_params(
        ledger_id=int(ledger_id),
        staging_ids=staging_ids,
        entry_anchor=entry_anchor,
        journal_uri=journal_uri,
        journal_entity_id=journal_entity_id,
        prior_sha256=prior_sha256,
    )
    print(f"params_written=cortex://{PARAMS_REL} ({dest})")

    if args.dry_run:
        print(f"dry_run: would project_ask submit prompt_uri={SEALED_URI}")
        return 0

    holder = f"tier2-digest-approve-{int(datetime.now(UTC).timestamp())}"
    cmd = [
        str(ROOT / "scripts" / "cortex" / "claude-ai-sync-jupiter"),
        "project-ask",
        "--register",
        "--purpose",
        "ask",
        "--no-uuid",
        "--model",
        "opus-5",
        "--prompt-uri",
        SEALED_URI,
        "--converse",
        "--delete-after=false",
        "--holder",
        holder,
    ]
    subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
