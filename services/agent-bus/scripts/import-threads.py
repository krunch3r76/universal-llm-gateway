#!/usr/bin/env python3
"""Import active markdown agent-threads into agent-bus turns DB.

Parses markdown thread files, extracts turns, and POSTs them to the
agent-bus API. Thread auto-created on first turn. Handles:
  - Duplicate turn numbers (later occurrence supersedes earlier)
  - Missing timestamps
  - Multi-addressee turns → to_agent='all'
  - Addendum/notice turns (non-standard header variants)

Usage:
    AGENT_BUS_TOKEN=... python scripts/import-threads.py [--dry-run]

Reads thread files from THREADS_DIR (default: ~/mcp-data/files/agent-threads/).
Posts to AGENT_BUS_URL (default: http://127.0.0.1:8100).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

THREADS_DIR = Path(
    os.environ.get("THREADS_DIR", os.path.expanduser("~/mcp-data/files/agent-threads"))
)
AGENT_BUS_URL = os.environ.get("AGENT_BUS_URL", "http://127.0.0.1:8100")
TOKEN = os.environ.get("AGENT_BUS_TOKEN", "")

ACTIVE_THREADS = [
    "017-journal-extraction-pipeline.md",
    "018-token-cost-observability.md",
    "022-event-role-architecture.md",
    "024-agent-bus-v2-turns-db.md",
]

AGENT_MAP = {
    "claude web": "web",
    "claude api": "api",
    "claude cursor": "cursor",
    "kaywan": "kaywan",
}

TURN_HEADER_RE = re.compile(
    r"^##\s+Turn\s+(\d+)(?:\s+\w+)?\s*—\s*(.+?)\s*—\s*(.+?)$",
    re.MULTILINE,
)

TO_LINE_RE = re.compile(
    r"^\*\*To:\*\*\s*(.+?)$",
    re.MULTILINE,
)


@dataclass
class ParsedTurn:
    original_number: int
    from_agent: str
    to_agent: str
    subject: str
    body: str
    created_at: str | None = None
    file_order: int = 0


@dataclass
class ImportResult:
    thread_id: str
    slug: str
    turns_imported: int = 0
    turns_superseded: int = 0
    anomalies: list[str] = field(default_factory=list)


def parse_agent(raw: str) -> str:
    """Map a raw agent string to an AgentName enum value."""
    normalized = raw.strip().lower()
    for prefix, agent_id in AGENT_MAP.items():
        if normalized.startswith(prefix):
            return agent_id
    return "all"


def parse_recipient(raw: str) -> str:
    """Parse the **To:** line into an AgentName."""
    normalized = raw.strip().lower()
    if "+" in normalized or "," in normalized or normalized.endswith("all"):
        return "all"
    return parse_agent(raw)


def parse_timestamp(raw: str) -> str | None:
    """Extract ISO timestamp from header timestamp portion.

    Handles: '2026-03-16 18:00 UTC', '2026-03-16 UTC', bare date.
    Returns ISO format string or None.
    """
    raw = raw.strip().rstrip("UTC").strip()
    parts = raw.split()
    if not parts:
        return None
    date_part = parts[0]
    time_part = parts[1] if len(parts) > 1 else None
    if time_part and re.match(r"\d{1,2}:\d{2}", time_part):
        return f"{date_part}T{time_part}:00Z"
    return f"{date_part}T00:00:00Z"


def extract_subject(body: str) -> str:
    """First non-empty line of body content as subject."""
    for line in body.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("---"):
            continue
        if line.startswith("**To:**"):
            continue
        line = line.lstrip("#").strip()
        line = line.lstrip("*").rstrip("*").strip()
        if line:
            return line[:120]
    return "(no subject)"


def parse_thread_file(filepath: Path) -> list[ParsedTurn]:
    """Parse a markdown thread file into a list of ParsedTurns."""
    content = filepath.read_text(encoding="utf-8")
    turns: list[ParsedTurn] = []

    headers = list(TURN_HEADER_RE.finditer(content))
    if not headers:
        return turns

    for i, match in enumerate(headers):
        turn_number = int(match.group(1))
        from_agent = parse_agent(match.group(2))
        timestamp = parse_timestamp(match.group(3))

        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(content)

        body_block = content[start:end].strip()
        if body_block.startswith("---"):
            body_block = body_block[3:].strip()

        to_match = TO_LINE_RE.search(body_block)
        if to_match:
            to_agent = parse_recipient(to_match.group(1))
            body_after_to = body_block[to_match.end() :].strip()
        else:
            to_agent = "all"
            body_after_to = body_block

        subject = extract_subject(body_after_to)

        turns.append(
            ParsedTurn(
                original_number=turn_number,
                from_agent=from_agent,
                to_agent=to_agent,
                subject=subject,
                body=body_block,
                created_at=timestamp,
                file_order=i,
            )
        )

    return turns


def api_request(
    method: str,
    path: str,
    body: dict | None = None,
) -> tuple[int, dict]:
    """Make an HTTP request to agent-bus API."""
    url = f"{AGENT_BUS_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def import_thread(
    filepath: Path,
    *,
    dry_run: bool = False,
) -> ImportResult:
    """Import a single thread file into agent-bus."""
    stem = filepath.stem
    parts = stem.split("-", 1)
    thread_id = parts[0]
    slug = parts[1] if len(parts) > 1 else stem

    result = ImportResult(thread_id=thread_id, slug=slug)
    turns = parse_thread_file(filepath)

    if not turns:
        result.anomalies.append("no turns found in file")
        return result

    original_to_db: dict[int, list[int]] = {}

    for turn in turns:
        if dry_run:
            print(
                f"  [DRY] Turn {turn.original_number} "
                f"from={turn.from_agent} to={turn.to_agent} "
                f"subject={turn.subject[:60]}"
            )
            result.turns_imported += 1
            continue

        payload: dict = {
            "thread": thread_id,
            "thread_slug": slug,
            "from": turn.from_agent,
            "to": turn.to_agent,
            "subject": turn.subject,
            "body": turn.body,
            "status": "open",
        }

        status_code, resp = api_request("POST", "/turns", payload)
        if status_code == 201:
            db_id = resp["id"]
            original_to_db.setdefault(turn.original_number, []).append(db_id)
            result.turns_imported += 1
            print(
                f"  ✓ Turn {turn.original_number} → DB id={db_id} "
                f"(turn_number={resp['turn_number']})"
            )
        else:
            result.anomalies.append(
                f"Turn {turn.original_number}: HTTP {status_code} — {json.dumps(resp)}"
            )
            print(f"  ✗ Turn {turn.original_number}: {status_code} {resp}")

    if not dry_run:
        for orig_num, db_ids in original_to_db.items():
            if len(db_ids) > 1:
                winner_id = db_ids[-1]
                for loser_id in db_ids[:-1]:
                    s, _ = api_request(
                        "PATCH",
                        f"/turns/{loser_id}/status",
                        {
                            "status": "superseded",
                            "supersedes_turn": winner_id,
                        },
                    )
                    if s == 200:
                        result.turns_superseded += 1
                        print(
                            f"  ⊘ DB id={loser_id} superseded by {winner_id} "
                            f"(original Turn {orig_num})"
                        )
                    else:
                        result.anomalies.append(
                            f"Failed to supersede DB id={loser_id}: HTTP {s}"
                        )

        # Historical imports are already processed — mark all as read
        s, resp = api_request(
            "GET", f"/turns?thread={thread_id}&mark_read=true&compact=true"
        )
        if s == 200:
            marked = sum(1 for t in resp.get("turns", []) if t.get("read_at"))
            print(f"  ✓ Marked {marked} imported turns as read")
        else:
            result.anomalies.append(f"Failed to mark-read: HTTP {s}")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Parse and print without POSTing"
    )
    args = parser.parse_args()

    if not TOKEN and not args.dry_run:
        print("ERROR: AGENT_BUS_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        try:
            status, resp = api_request("GET", "/health")
        except Exception as e:
            print(
                f"ERROR: Cannot reach agent-bus at {AGENT_BUS_URL}: {e}",
                file=sys.stderr,
            )
            sys.exit(1)
        if status != 200:
            print(f"ERROR: agent-bus unhealthy: {resp}", file=sys.stderr)
            sys.exit(1)

    results: list[ImportResult] = []
    for filename in ACTIVE_THREADS:
        filepath = THREADS_DIR / filename
        if not filepath.exists():
            print(f"SKIP: {filepath} not found")
            continue
        print(f"\n{'=' * 60}")
        print(f"Importing: {filename} → thread={filepath.stem.split('-', 1)[0]}")
        print(f"{'=' * 60}")
        result = import_thread(filepath, dry_run=args.dry_run)
        results.append(result)

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    total_turns = 0
    total_superseded = 0
    total_anomalies = 0
    for r in results:
        total_turns += r.turns_imported
        total_superseded += r.turns_superseded
        total_anomalies += len(r.anomalies)
        status = "✓" if not r.anomalies else "⚠"
        print(
            f"  {status} Thread {r.thread_id} ({r.slug}): "
            f"{r.turns_imported} turns, {r.turns_superseded} superseded"
            f"{f', {len(r.anomalies)} anomalies' if r.anomalies else ''}"
        )
        for a in r.anomalies:
            print(f"    ⚠ {a}")
    print(
        f"\nTotal: {total_turns} turns imported, "
        f"{total_superseded} superseded, {total_anomalies} anomalies"
    )


if __name__ == "__main__":
    main()
