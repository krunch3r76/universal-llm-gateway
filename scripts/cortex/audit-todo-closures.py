#!/usr/bin/env python3
"""Audit todo closure gaps — surface done todos with no audit trail.

Per todo:cortex-todo-closure-payload AC7. Lists every cortex `todo` entity
with `workflow_state='done'` and zero assertions (the worked example was
todo:email-bridge-auto-pull-mailbox, closed without any audit trail).

This script is observational, not interventional. It does not call
`pipeline:todo-close` to backfill — synthesizing a closure payload requires
reading the closing session's transcript, which is an off-critical-path
agent task (web's domain per the spec). The script's job is to enumerate
the gaps so that backfill can be scheduled.

Usage::

    python scripts/cortex/audit-todo-closures.py              # text report
    python scripts/cortex/audit-todo-closures.py --json       # JSON output
    python scripts/cortex/audit-todo-closures.py --markdown   # markdown table
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "libs"))

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402


def _list_done_todos(client: Any) -> list[dict[str, Any]]:
    resp = client.get(
        "/entities", params={"type": "todo", "workflow_state": "done", "limit": 500}
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def _entity_assertion_count(client: Any, entity_id: str) -> int:
    resp = client.get(f"/entities/{entity_id}")
    if resp.status_code == 404:
        return -1
    resp.raise_for_status()
    return len(resp.json().get("assertions", []) or [])


def _audit(client: Any) -> dict[str, Any]:
    todos = _list_done_todos(client)
    gaps: list[dict[str, Any]] = []
    healthy: list[str] = []
    for t in todos:
        eid = t.get("id")
        if not eid:
            continue
        n = _entity_assertion_count(client, eid)
        if n == 0:
            gaps.append(
                {
                    "id": eid,
                    "name": t.get("name", ""),
                    "created_at": t.get("created_at", ""),
                }
            )
        elif n > 0:
            healthy.append(eid)
    return {
        "total_done": len(todos),
        "with_audit_trail": len(healthy),
        "closure_gaps": gaps,
        "gap_count": len(gaps),
    }


def _render_text(report: dict[str, Any]) -> str:
    lines = [
        "# Todo Closure Audit",
        "",
        f"- total done todos: {report['total_done']}",
        f"- with audit trail (≥1 assertion): {report['with_audit_trail']}",
        f"- closure gaps (0 assertions): {report['gap_count']}",
        "",
    ]
    if report["closure_gaps"]:
        lines.append("## Gaps")
        lines.append("")
        for g in report["closure_gaps"]:
            lines.append(f"- {g['id']}  —  {g['name']}  ({g['created_at']})")
    else:
        lines.append("No closure gaps detected.")
    return "\n".join(lines)


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "| id | name | created_at |",
        "|---|---|---|",
    ]
    for g in report["closure_gaps"]:
        lines.append(f"| `{g['id']}` | {g['name']} | {g['created_at']} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    parser.add_argument(
        "--markdown", action="store_true", help="emit markdown gap table only"
    )
    args = parser.parse_args()

    with make_sync_client(DEFAULT_CORTEX_URL, timeout=15.0) as client:
        report = _audit(client)

    if args.json:
        print(json.dumps(report, indent=2))
    elif args.markdown:
        print(_render_markdown(report))
    else:
        print(_render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
