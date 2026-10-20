"""Shared liaison tick and IDE hop entry points (MCP continuity + CLI scripts)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from bus_watch.digest_budget import _bus, _get
from bus_watch.fable_lock import WATCH_DIR, read_lock, refresh_fable_lock
from bus_watch.hop_qualify import hop_qualifies
from bus_watch.ide_hop import (
    DEFAULT_REMOTE_REPO,
    build_hop_preamble,
    build_ide_hop_message,
    finalize_xvfb_hop_sidebar,
    find_transcript_id,
    fire_ide_hop,
    live_watcher_labels,
    policy_gui_host,
    policy_judgment_posture,
    seal_hop_window,
    tick_register,
)
from bus_watch.ide_hop_retire import (
    GOAL_RELEASE,
    RETIRED_TAB_PREFIX,
    quiesce_departing_tab,
    retire_departing_tab,
)
from bus_watch.judgment_rows import harvest_judgment_turns
from bus_watch.liaison_digest import build_digest, effective_policy
from bus_watch.now_row import format_now_line, resolve_now_row
from bus_watch.tick_state import load_state, save_state
from cursor_bridge.cursor_agents_hop import is_cursor_xvfb_host


def liaison_tick_once(
    root: str,
    *,
    register: str | None = None,
    state_file: Path | str | None = None,
    holder: str | None = None,
) -> dict[str, Any]:
    """Build and persist one liaison digest (``liaison-tick.py --once``)."""
    root_id = str(root).strip()
    if not root_id:
        return {"ok": False, "reason": "missing_root"}
    state_path = (
        Path(state_file)
        if state_file
        else WATCH_DIR / f"liaison-{root_id}.tick.json"
    )
    state = load_state(state_path)
    if register:
        state["register"] = register
    register_val = str(state.get("register") or "attended")
    digest = build_digest(root_id, state, register=register_val)
    holder_id = str(holder or "").strip()
    if holder_id and read_lock(root_id).get("holder") == holder_id:
        policy = digest.get("policy") or {}
        refresh_fable_lock(
            holder_id,
            root_id=root_id,
            max_hop_minutes=float(policy.get("max_hop_minutes") or 60),
            turns_seen=int((digest.get("root") or {}).get("turns") or 0),
        )
    save_state(state_path, state)
    return digest


def _jsonl_turn_count(transcript_id: str) -> int | None:
    from bus_watch.ide_budget import AGENT_TRANSCRIPTS
    from cortex_store.dispatch_ops.ops_transcript_discover import _live_jsonl_turn_count
    from cortex_store.transcript_session_id import jsonl_path_for_uuid

    path = jsonl_path_for_uuid(AGENT_TRANSCRIPTS, transcript_id)
    if not path.is_file():
        return None
    try:
        return _live_jsonl_turn_count(path)
    except (OSError, ValueError):
        return None


def _post_root_checkpoint_factory(transcript_id: str | None) -> Callable[[str, str], bool]:
    def _post_root_checkpoint(root_id: str, body: str) -> bool:
        lines: list[str] = []
        if transcript_id:
            turn_count = _jsonl_turn_count(transcript_id)
            if turn_count is not None:
                lines.append(
                    f"Window: transcript_id={transcript_id} · turns@cp={turn_count}"
                )
        lines.append(body)
        full_body = "\n".join(lines)
        with _bus() as client:
            r = client.post(
                "/turns",
                json={
                    "thread": root_id,
                    "from": "cursor",
                    "to": "cursor",
                    "subject": f"HARVEST judgment — {root_id}",
                    "body": full_body,
                },
            )
            return r.status_code < 400

    return _post_root_checkpoint


def _mark_turns_read(root_id: str, turn_numbers: list[int]) -> None:
    with _bus() as client:
        client.patch(
            f"/threads/{root_id}/turns/read-state",
            json={"turn_numbers": turn_numbers, "agent": "cursor"},
        )


def _fetch_turns(root_id: str, **params: object) -> dict:
    with _bus() as client:
        return _get(client, "/turns", thread=root_id, **params) or {}


def liaison_ide_hop(
    root: str,
    *,
    transcript_id: str | None = None,
    row: str | None = None,
    arm_labels: list[str] | None = None,
    no_auto_arm: bool = False,
    tip_cp: int | None = None,
    gui_host: str | None = None,
    remote_repo: str = DEFAULT_REMOTE_REPO,
    no_raise: bool = False,
    model_query: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    exclude_lane: list[str] | None = None,
    find_transcript: str | None = None,
) -> dict[str, Any]:
    """Run attended IDE hop (``liaison-ide-hop.py`` body)."""
    if find_transcript:
        tid = find_transcript_id(find_transcript)
        return {"ok": tid is not None, "transcript_id": tid}

    root_id = str(root).strip()
    labels = list(arm_labels or [])
    if not no_auto_arm:
        labels.extend(
            lbl
            for lbl in live_watcher_labels(
                root_id, exclude_threads=exclude_lane or []
            )
            if lbl not in labels
        )
    tid = str(transcript_id or "").strip()
    if not tid:
        from bus_watch.ide_budget import (
            AGENT_TRANSCRIPTS,
            active_budget_excludes,
            ide_holder_transcript,
            newest_resume_transcript,
        )

        lock = read_lock(root_id)
        tid = ide_holder_transcript(lock) or newest_resume_transcript(
            root_id,
            AGENT_TRANSCRIPTS,
            exclude=active_budget_excludes(root_id),
        )
    if not tid:
        return {
            "ok": False,
            "phase": "seal_transcript_unknown",
            "root": root_id,
            "fix": "pass transcript_id for the departing tab uuid",
        }
    state_path = WATCH_DIR / f"liaison-{root_id}.tick.json"
    state = load_state(state_path)
    policy = effective_policy(state)
    now_row_set_at = state.get("now_row_set_at")
    harvest = harvest_judgment_turns(
        root_id,
        get_turns=_fetch_turns,
        post_checkpoint=_post_root_checkpoint_factory(tid),
        mark_read=_mark_turns_read,
        now_row_set_at=str(now_row_set_at or "") or None,
    )
    if not harvest.get("ok"):
        return {"ok": False, "phase": "harvest_failed", **harvest}
    digest = build_digest(
        root_id,
        state,
        register=str(state.get("register") or "attended"),
    )
    raw_row, row_source = resolve_now_row(digest)
    row_line = format_now_line(
        raw_row or row, row_source, digest, omit_tip_prefix=True
    )
    if not row_line.strip():
        row_line = (
            (row or "").strip()
            or str(policy.get("now_row") or "").strip()
            or "operator-requested hop"
        )
    qualify = hop_qualifies(row=row_line, arm_labels=labels, policy=policy)
    if not qualify["ok"] and not force:
        return {
            "ok": False,
            "phase": "no_autonomous_followup",
            "stay": True,
            "root": root_id,
            "harvest": harvest,
            **qualify,
        }
    reg = tick_register(root_id)
    preamble = build_hop_preamble(
        root_id,
        row=row_line,
        arm_labels=labels,
        tip_cp_ordinal=tip_cp,
        register=reg,
    )
    seal = seal_hop_window(
        root_id,
        transcript_id=tid,
        preamble=preamble,
    )
    if not seal.get("ok"):
        return {"ok": False, "root": root_id, **seal}
    tip_cp_val = tip_cp
    if tip_cp_val is None and seal.get("bus_turn") is not None:
        tip_cp_val = int(seal["bus_turn"])
    message = build_ide_hop_message(
        root_id,
        row=row_line,
        arm_labels=labels,
        tip_cp_ordinal=tip_cp_val,
        register=tick_register(root_id),
        judgment_posture=policy_judgment_posture(root_id),
    )

    def _on_fired() -> dict:
        return quiesce_departing_tab(root_id, transcript_id=tid)

    out = fire_ide_hop(
        message,
        root_id=root_id,
        seal=seal,
        gui_host=gui_host or policy_gui_host(root_id),
        remote_repo=remote_repo,
        dry_run=dry_run,
        no_raise=no_raise,
        departing_transcript_id=tid,
        on_fired=None if dry_run else _on_fired,
        model_query=model_query,
    )
    out["arm_labels"] = labels
    out["seal"] = seal
    out["message"] = message
    out["harvest"] = harvest
    out["row_source"] = row_source
    if out.get("ok"):
        out["goal_release"] = GOAL_RELEASE
        out["retire_tab_prefix"] = RETIRED_TAB_PREFIX
        out["retire_tab_suffix"] = " (retired)"
        if dry_run:
            out["retire"] = {"ok": True, "skipped": "dry_run"}
        else:
            out["retire"] = retire_departing_tab(
                root_id,
                holder=f"ide:{tid}",
                transcript_id=tid,
            )
            gui = gui_host or policy_gui_host(root_id)
            landed_tid = str(out.get("landed_transcript_id") or "")
            if is_cursor_xvfb_host(gui) and tid:
                slug_fragment = (
                    str((digest.get("root") or {}).get("slug") or "")
                    or str(state.get("slug") or "")
                    or ""
                ).strip()
                try:
                    xvfb_out = finalize_xvfb_hop_sidebar(
                        root_id,
                        departing_agent_id=tid,
                        landed_agent_id=landed_tid,
                        slug_fragment=slug_fragment,
                    )
                    out.update(xvfb_out)
                except Exception as exc:  # noqa: BLE001
                    out["xvfb_finalize"] = {
                        "ok": False,
                        "reason": f"finalize_exception:{type(exc).__name__}",
                    }
    elif out.get("phase") == "expired":
        out.setdefault("operator_gate", True)
        out.setdefault(
            "fix",
            "OPERATOR_GATE: hop expired without land proof — paste once from "
            f"message_path={out.get('message_path')}; do not Ctrl+N.",
        )
    return out


__all__ = ["liaison_ide_hop", "liaison_tick_once"]
