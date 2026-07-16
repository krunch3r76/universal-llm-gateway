#!/usr/bin/env python3
"""Fable settled-gate falsifiers for CDP ask lane (4917).

Prove degenerate turns cannot be harvested-and-deleted.
Run ON Jupiter with BROWSER_CDP_URL pointing at the parallel lane (:9223).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.chat_reply_wait import (  # noqa: E402
    HarvestIncomplete,
    harvest_assistant,
    wait_assistant_reply,
)
from claude_bundles.chat_session_hygiene import (  # noqa: E402
    goto_fresh_compose,
    in_active_chat,
    pick_chat_page,
)
from claude_bundles.project_ask import run_project_ask  # noqa: E402
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp  # noqa: E402

OUT = Path(
    "/mnt/torus/mcp-data/files/notes/system/threads/4917-fable-cdp-review/falsifiers"
)
PROJECT = "019f6917-2ab2-772c-a1ec-f88434b08e32"
CDP = DEFAULT_CDP_URL


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(name: str, payload: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


async def f1_timeout_incomplete_no_delete() -> dict:
    """Short timeout + high min_body ⇒ incomplete; chat must not be deleted."""
    result = await run_project_ask(
        "Reply with a single word: PING. Do not elaborate.",
        project_uuid=PROJECT,
        model="opus-4.8",
        delete_after=True,
        cdp_url=CDP,
        timeout_s=8,
        min_growth=50,
        min_body=8000,  # impossible for a one-word reply
        archive_path=str(OUT / "f1-would-have-archived.md"),
    )
    deleted = bool(result.delete_after and result.delete_after.get("ok") and
                   result.delete_after.get("step") != "skip_not_in_chat")
    # Also: if delete ran from an active chat, deleted_from would be set
    if result.delete_after and result.delete_after.get("deleted_from"):
        deleted = True
    passed = (not result.ok) and (result.delete_after is None or not deleted)
    return {
        "falsifier": "F1_timeout_incomplete_no_delete",
        "passed": passed,
        "stamp": _stamp(),
        "result": result.as_dict(),
        "criterion": "ok=False ∧ delete did not destroy a chat",
        "note": "min_body=8000 with 8s timeout forces HarvestIncomplete path",
    }


async def f2_error_banner_no_delete() -> dict:
    """Inject error banner into DOM; wait must raise HarvestIncomplete; ¬delete."""
    pw, _browser, ctx, _ = await connect_cdp(CDP)
    try:
        page = await pick_chat_page(ctx)
        await goto_fresh_compose(page, project_uuid=PROJECT)
        url_before = page.url
        await page.evaluate(
            """() => {
              const d = document.createElement('div');
              d.id = 'falsifier-error-banner';
              d.setAttribute('role', 'alert');
              d.textContent = 'Something went wrong — rate limit. Try again later.';
              document.body.prepend(d);
            }"""
        )
        # Prove harvest sees the banner before waiting.
        probe = await harvest_assistant(page, min_msg_chars=10)
        before = {"body_len": 0, "n": 0}
        raised = None
        try:
            await wait_assistant_reply(
                page,
                before=before,
                timeout_s=5,
                poll_ms=400,
                min_growth=10,
                min_body=20,
            )
            raised = None
        except HarvestIncomplete as exc:
            raised = str(exc)
        except Exception as exc:  # noqa: BLE001
            raised = f"other:{exc}"
        passed = bool(
            probe.get("error_banner")
            and raised
            and "error_banner" in raised
        )
        return {
            "falsifier": "F2_error_banner_no_delete",
            "passed": passed,
            "stamp": _stamp(),
            "probe_error_banner": probe.get("error_banner"),
            "raised": raised,
            "url_before": url_before,
            "url_after": page.url,
            "criterion": "harvest.error_banner ∧ HarvestIncomplete(error_banner) ∧ no delete",
            "note": "Banner prepended; scan head+tail of page text",
        }
    finally:
        await pw.stop()


async def f3_missing_archive_refuses_delete() -> dict:
    """delete_after=True without archive_path ⇒ refuse; ¬delete."""
    result = await run_project_ask(
        "Reply with exactly: F3_ARCHIVE_GATE",
        project_uuid=PROJECT,
        model="opus-4.8",
        delete_after=True,
        cdp_url=CDP,
        timeout_s=120,
        min_growth=20,
        min_body=10,
        archive_path=None,  # forbidden when deleting
    )
    refused = "archive_path required" in (result.error or "")
    no_delete = result.delete_after is None
    # If harvest succeeded but delete refused, body may be present
    passed = refused and no_delete and (not result.ok)
    # Edge: if wait failed for other reasons, still check archive refuse when body present
    if result.body and refused and no_delete:
        passed = True
    return {
        "falsifier": "F3_missing_archive_refuses_delete",
        "passed": passed,
        "stamp": _stamp(),
        "result": result.as_dict(),
        "criterion": "error mentions archive_path ∧ delete_after is null ∧ ok=False",
    }


async def main() -> int:
    reports = []
    for name, coro in (
        ("f1.json", f1_timeout_incomplete_no_delete()),
        ("f2.json", f2_error_banner_no_delete()),
        ("f3.json", f3_missing_archive_refuses_delete()),
    ):
        print(f"running {name}…", flush=True)
        try:
            report = await coro
        except Exception as exc:  # noqa: BLE001
            report = {
                "falsifier": name,
                "passed": False,
                "stamp": _stamp(),
                "error": str(exc),
            }
        path = _write(name, report)
        print(json.dumps({"wrote": str(path), "passed": report.get("passed")}, indent=2))
        reports.append(report)

    summary = {
        "stamp": _stamp(),
        "all_passed": all(r.get("passed") for r in reports),
        "results": [
            {"id": r.get("falsifier"), "passed": r.get("passed")} for r in reports
        ],
    }
    _write("SUMMARY.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
