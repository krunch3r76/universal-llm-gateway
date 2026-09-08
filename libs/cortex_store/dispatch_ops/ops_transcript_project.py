"""Cortex dispatch op ``transcript_project`` — mechanical window×CP projection."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from durable_io.atomic import durable_rmw_text, durable_write_text
from universal_logging import get_logger

from ..events_projection import (
    cortex_transcript_projected,
    cortex_transcript_projection_anchor_mismatch,
    cortex_transcript_projection_refused,
)
from ..transcript_assembly import _read_jsonl, _transcripts_root
from ..transcript_projection_facts import WindowFacts, parse_records
from ..transcript_projection_membership import (
    boundary_sends_for_lane,
    build_bus_turn_index,
    classify_membership,
    detect_anchor_mismatches,
    extract_closeout_note,
    extract_cp_highlight,
    extract_cp_note,
    fetch_lineage_children,
    fetch_thread_detail,
    join_boundary_turn_number,
    jsonl_candidates,
    now_iso,
    parse_iso_datetime,
    prose_tail_for_range,
)
from ..transcript_projection_render import (
    derive_open_interval,
    render_projection_markdown,
    sha256_text,
)
from ..transcript_projection_state import (
    CellFacts,
    compact_state,
    empty_state,
    facts_to_cell_dict,
    load_state,
    state_transform_merge,
)
from ._shared import _FILES_ROOT

logger = get_logger("cortex-api.dispatch_ops.transcript_project")

_THREADS_SUBDIR = ("notes", "system", "threads")


def _sidecar_paths(thread: str) -> tuple[Path, Path, str, str]:
    root = _FILES_ROOT.joinpath(*_THREADS_SUBDIR)
    state_path = root / f"{thread}-transcript-projection.state.json"
    md_path = root / f"{thread}-transcript-projection.md"
    state_uri = f"cortex://notes/system/threads/{thread}-transcript-projection.state.json"
    md_uri = f"cortex://notes/system/threads/{thread}-transcript-projection.md"
    return state_path, md_path, state_uri, md_uri


def _refuse(thread: str, code: str) -> dict[str, Any]:
    cortex_transcript_projection_refused(thread=thread, code=code)
    return {"error": code, "reason": code, "thread": thread}


def _is_root_thread(detail: dict[str, Any]) -> bool:
    from agent_bus_store.thread_classification import classify_thread

    tags = detail.get("tags") or []
    if isinstance(tags, list) and "spine=root" in tags:
        return True
    return classify_thread(tags if isinstance(tags, list) else [])["spine"] == "root"


def _boundary_body(send: Any, bus_index: Any) -> str:
    """Return agent-bus body for a JSONL-observed boundary send, if indexed."""
    row = bus_index.by_thread_subject.get((send.thread, send.subject))
    if row is None:
        return ""
    return str(row.get("body") or "")


def _window_patch_from_facts(
    facts: WindowFacts,
    *,
    root: str,
    children: list[str],
    bus_index: Any,
    closed_hi: int,
    first_seen: str,
    source: str = "live",
    jsonl_stat: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Build window state patch and new closed cells from facts."""
    boundaries = boundary_sends_for_lane(facts, root, children)
    cells: list[dict[str, Any]] = []
    new_closed_hi = closed_hi
    projected_at = now_iso()
    for send in boundaries:
        if send.turn_index <= closed_hi:
            continue
        turn_number, cp_ordinal = join_boundary_turn_number(send, bus_index)
        boundary_body = _boundary_body(send, bus_index)
        if send.kind == "CHECKPOINT":
            note, note_source = extract_cp_note(boundary_body, send.subject)
        else:
            note, note_source = extract_closeout_note(send.subject, boundary_body)
        lo = new_closed_hi
        hi = send.turn_index
        cell: dict[str, Any] = {
            "turn_lo": lo,
            "turn_hi": hi,
            "boundary": {
                "kind": send.kind,
                "thread": send.thread,
                "subject": send.subject,
                "subject_head": send.subject_head,
                "turn_number": turn_number,
                "cp_ordinal": cp_ordinal,
                "supersedes_turn": send.supersedes_turn,
            },
            "note": note,
            "note_source": note_source,
            "facts": _facts_dict(facts, lo, hi),
            "projected_at": projected_at,
            "compacted": False,
        }
        if send.kind == "CHECKPOINT":
            highlight = extract_cp_highlight(boundary_body)
            if highlight is not None:
                cell["highlight"] = highlight
        cells.append(cell)
        new_closed_hi = hi
    open_tail = None
    if facts.turn_count > new_closed_hi:
        lo = new_closed_hi
        hi = facts.turn_count
        open_tail = {
            "turn_lo": lo,
            "turn_hi": hi,
            "boundary": None,
            "note": prose_tail_for_range(facts, lo, hi),
            "note_source": "prose_tail",
            "facts": _facts_dict(facts, lo, hi),
            "projected_at": projected_at,
            "compacted": False,
        }
    tab_title = facts.tab_titles[-1] if facts.tab_titles else facts.transcript_id[:8]
    patch = {
        "lane": root,
        "binding": "dominant_write",
        "tab_title": tab_title,
        "tab_titles": facts.tab_titles,
        "opening_ask": facts.opening_ask,
        "first_seen": first_seen or projected_at,
        "jsonl": jsonl_stat or {"present": True},
        "source": source,
        "turn_count": facts.turn_count,
        "closed_hi": new_closed_hi,
        "cells": cells,
        "open_tail": open_tail,
        "terminal": None,
    }
    return patch, cells, new_closed_hi


def _facts_dict(facts: WindowFacts, turn_lo: int, turn_hi: int) -> dict[str, Any]:
    cf = CellFacts(
        asks=[a for a in facts.asks if a][:3],
        bus_sends=[
            {
                "thread": s.thread,
                "kind": s.kind,
                "subject_head": s.subject_head,
                "supersedes_turn": s.supersedes_turn,
            }
            for s in facts.bus_sends
            if turn_lo < s.turn_index <= turn_hi
        ],
        bus_touch=facts.bus_touch,
        artifacts=facts.artifacts,
        code_paths=facts.code_paths,
        dispatches=[
            {
                "tool": d.tool,
                "op": d.op,
                "seat_or_model": d.seat_or_model,
                "contract": d.contract,
                "ref": d.ref,
                "ref_source": d.ref_source,
            }
            for d in facts.dispatches
        ],
        sha_mentions=facts.sha_mentions,
        harness_errors=facts.harness_errors,
        prose_tail=prose_tail_for_range(facts, turn_lo, turn_hi),
    )
    return facts_to_cell_dict(cf)


def run_transcript_project(
    *,
    thread: str,
    children_policy: str = "sub_mission",
    children: list[str] | None = None,
    render_budget_bytes: int = 98_304,
    dry_run: bool = False,
    transcript_ids: list[str] | None = None,
    bus_get: Callable[[str], Any] | None = None,
    transcripts_root: Path | None = None,
) -> dict[str, Any]:
    """Core projection runner — shared by dispatch op and tests."""
    started = time.monotonic()
    if not thread:
        return _refuse("", "transcript_project.missing_thread")
    detail = fetch_thread_detail(thread, bus_get=bus_get)
    if detail is None:
        return _refuse(thread, "transcript_project.thread_not_found")
    if not _is_root_thread(detail):
        return _refuse(thread, "transcript_project.not_root")
    if children is None:
        if children_policy == "none":
            children = []
        elif children_policy == "sub_mission":
            children = fetch_lineage_children(thread, bus_get=bus_get)
        else:
            children = [str(c) for c in children]
    state_path, md_path, state_uri, md_uri = _sidecar_paths(thread)
    prior_state_text = state_path.read_text(encoding="utf-8") if state_path.is_file() else ""
    try:
        prior_state = load_state(prior_state_text) if prior_state_text.strip() else empty_state(thread, children)
    except ValueError:
        return _refuse(thread, f"transcript_project.state_corrupt{{path={state_path}}}")
    sticky = set(prior_state.get("windows", {}).keys())
    force_ids = set(transcript_ids or [])
    root_path = transcripts_root or _transcripts_root()
    created_at = parse_iso_datetime(str(detail.get("created_at") or ""))
    candidates = jsonl_candidates(
        transcripts_root=root_path,
        thread_created_at=created_at,
        sticky_ids=sticky,
        force_ids=force_ids,
    )
    bus_index = build_bus_turn_index([thread, *children], bus_get=bus_get)
    excluded_counts: dict[str, int] = {}
    source_counts = {"live": 0, "sealed": 0, "unavailable": 0}
    parsed = 0
    windows_changed = 0
    cells_added = 0
    window_updates: dict[str, dict[str, Any]] = {}
    member_facts: dict[str, WindowFacts] = {}
    delta: list[dict[str, Any]] = []
    for path in candidates:
        tid = path.parent.name
        stat = path.stat()
        jsonl_stat = {
            "present": True,
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        }
        prior_window = prior_state.get("windows", {}).get(tid, {})
        prior_jsonl = prior_window.get("jsonl") or {}
        if (
            tid in prior_state.get("windows", {})
            and prior_jsonl.get("present")
            and prior_jsonl.get("mtime") == stat.st_mtime
            and prior_jsonl.get("size") == stat.st_size
        ):
            member_facts[tid] = parse_records(_read_jsonl(path), tid)
            continue
        records = _read_jsonl(path)
        facts = parse_records(records, tid)
        parsed += 1
        membership = classify_membership(
            facts,
            root=thread,
            children=children,
            sticky=tid in sticky,
        )
        if not membership.member:
            reason = membership.exclude_reason or "dropped"
            excluded_counts[reason] = excluded_counts.get(reason, 0) + 1
            continue
        member_facts[tid] = facts
        closed_hi = int(prior_window.get("closed_hi", 0))
        patch, new_cells, _ = _window_patch_from_facts(
            facts,
            root=thread,
            children=children,
            bus_index=bus_index,
            closed_hi=closed_hi,
            first_seen=prior_window.get("first_seen") or now_iso(),
            jsonl_stat=jsonl_stat,
        )
        patch["lane"] = membership.lane
        patch["binding"] = membership.binding
        window_updates[tid] = patch
        windows_changed += 1
        cells_added += len(new_cells)
        source_counts["live"] += 1
        for cell in new_cells:
            delta.append(
                {
                    "transcript_id": tid,
                    "tab_title": patch["tab_title"],
                    "lane": patch["lane"],
                    "cell": cell,
                    "change": "added",
                }
            )
        if patch.get("open_tail"):
            delta.append(
                {
                    "transcript_id": tid,
                    "tab_title": patch["tab_title"],
                    "lane": patch["lane"],
                    "cell": patch["open_tail"],
                    "change": "open_tail",
                }
            )
    anchor_rows = [
        {
            "cp_turn": m.cp_turn,
            "cp_ordinal": m.cp_ordinal,
            "claimed": m.claimed,
            "observed": m.observed,
            "kind": m.kind,
        }
        for m in detect_anchor_mismatches(thread, bus_get=bus_get, member_facts=member_facts)
    ]
    prior_anchor = prior_state.get("anchor_mismatches") or []
    anchor_changed = anchor_rows != prior_anchor
    for row in anchor_rows:
        cortex_transcript_projection_anchor_mismatch(
            thread=thread, cp_turn=row["cp_turn"], kind=row["kind"]
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    last_run = {
        "windows_scanned": len(candidates),
        "parsed": parsed,
        "changed": windows_changed,
        "cells_added": cells_added,
        "elapsed_ms": elapsed_ms,
    }
    open_line = _build_open_line(
        thread,
        prior_state,
        window_updates,
        anchor_rows,
        excluded_counts,
        source_counts,
        last_run,
    )
    if dry_run or (not window_updates and not anchor_changed):
        return {
            "open_line": open_line,
            "delta": delta,
            "anchor_mismatches": anchor_rows,
            "projection": {
                "uri": md_uri,
                "state_uri": state_uri,
                "dry_run": dry_run,
            },
            "summary": f"transcript_project {thread}: dry={dry_run} parsed={parsed}",
        }
    updated_at = now_iso()
    transform_args = {
        "thread": thread,
        "children": children,
        "window_updates": window_updates,
        "anchor_mismatches": anchor_rows,
        "last_run": last_run,
        "updated_at": updated_at,
    }

    def _transform(before: str) -> str:
        return state_transform_merge(before, **transform_args)

    rmw = durable_rmw_text(
        state_path,
        _transform,
        retain_store_root=_FILES_ROOT,
        create_if_absent=True,
    )
    state_data = json.loads(rmw.after_text)
    compact_state(state_data)
    state_path.write_text(json.dumps(state_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    state_sha = sha256_text(rmw.after_text)
    prior_md = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    md_text, collapsed = render_projection_markdown(
        state_data,
        state_sha256=state_sha,
        render_budget_bytes=render_budget_bytes,
        prior_md=prior_md,
    )
    durable_write_text(md_path, md_text, retain_store_root=_FILES_ROOT)
    md_sha = sha256_text(md_text)
    cortex_transcript_projected(
        thread=thread,
        windows_scanned=len(candidates),
        parsed=parsed,
        windows_changed=windows_changed,
        cells_added=cells_added,
        anchor_mismatches=len(anchor_rows),
        elapsed_ms=elapsed_ms,
    )
    return {
        "open_line": open_line,
        "delta": delta,
        "anchor_mismatches": anchor_rows,
        "projection": {
            "uri": md_uri,
            "sha256": md_sha,
            "state_uri": state_uri,
            "state_sha256": state_sha,
            "bytes": len(md_text.encode("utf-8")),
            "collapsed_windows": collapsed,
        },
        "summary": (
            f"transcript_project {thread}: windows={len(state_data.get('windows', {}))} "
            f"cells_added={cells_added} mismatches={len(anchor_rows)}"
        ),
    }


def _build_open_line(
    thread: str,
    prior_state: dict[str, Any],
    window_updates: dict[str, Any],
    anchor_rows: list[dict[str, Any]],
    excluded_counts: dict[str, int],
    source_counts: dict[str, int],
    last_run: dict[str, Any],
) -> dict[str, Any]:
    windows = {**prior_state.get("windows", {}), **window_updates}
    latest_tid = next(reversed(window_updates), None) or next(iter(windows), None)
    latest = windows.get(latest_tid, {}) if latest_tid else {}
    merged_state = {**prior_state, "windows": windows, "anchor_mismatches": anchor_rows}
    return {
        "thread": thread,
        "windows": len(windows),
        "cells": sum(len(w.get("cells", [])) for w in windows.values()),
        "open_tails": sum(1 for w in windows.values() if w.get("open_tail")),
        "open_interval": derive_open_interval(merged_state),
        "latest_window": {
            "transcript_id": latest_tid,
            "tab_title": latest.get("tab_title"),
            "lane": latest.get("lane"),
            "turn_count": latest.get("turn_count"),
            "closed_hi": latest.get("closed_hi"),
            "last_note": (latest.get("cells") or [{}])[-1].get("note") if latest.get("cells") else None,
        }
        if latest_tid
        else None,
        "since_last_run": {
            "windows_changed": last_run.get("changed", 0),
            "cells_added": last_run.get("cells_added", 0),
            "cells_updated": 0,
            "joins_backfilled": 0,
        },
        "anchor_mismatches": len(anchor_rows),
        "excluded_counts": excluded_counts,
        "source_counts": source_counts,
        "elapsed_ms": last_run.get("elapsed_ms", 0),
    }


def _op_transcript_project(
    thread: str | None = None,
    children: list[str] | None = None,
    children_policy: str = "sub_mission",
    render_budget_bytes: int = 98_304,
    dry_run: bool = False,
    transcript_ids: list[str] | None = None,
    **_: object,
) -> dict[str, Any]:
    """Project Cursor windows into a derived transcript sidecar for a root lane."""
    if render_budget_bytes < 16_384 or render_budget_bytes > 524_288:
        render_budget_bytes = 98_304
    return run_transcript_project(
        thread=str(thread or ""),
        children_policy=children_policy,
        children=children,
        render_budget_bytes=render_budget_bytes,
        dry_run=bool(dry_run),
        transcript_ids=transcript_ids,
    )


__all__ = ["_op_transcript_project", "run_transcript_project"]
