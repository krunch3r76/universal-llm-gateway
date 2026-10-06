"""Lane ordering and hop-harvest lane records."""

from __future__ import annotations

import json
import re
from typing import Any

_CLOSEOUT_SUBJECT_RE = re.compile(r"^(?:\S+\s+)?CLOSEOUT\b")
_FENCE_OPEN_RE = re.compile(r"^\s*```(?:json)?\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")

try:
    from operator_hop_harvest import (
        assemble_operator_hop_view,
        parse_conductor_closeout,
        parse_harvest_recipe,
        parse_wait_block,
    )

    HOP_HARVEST_AVAILABLE = True
except ImportError:
    HOP_HARVEST_AVAILABLE = False


def order_lanes_newest_first(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(children, key=lambda c: int(c.get("thread_id") or 0), reverse=True)


def _first_non_empty_line(text: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s
    return None


def _closeout_json_object(body: str) -> dict[str, Any] | None:
    stripped = body.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None
    lines = body.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not _FENCE_OPEN_RE.match(lines[i]):
        return None
    i += 1
    chunk: list[str] = []
    while i < len(lines):
        if _FENCE_CLOSE_RE.match(lines[i]):
            break
        chunk.append(lines[i])
        i += 1
    try:
        obj = json.loads("\n".join(chunk))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def is_closeout_turn(turn: dict[str, Any]) -> bool:
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    if _CLOSEOUT_SUBJECT_RE.match(subject):
        return True
    if _first_non_empty_line(body) == "TYPE: CLOSEOUT":
        return True
    obj = _closeout_json_object(body)
    return (
        isinstance(obj, dict)
        and "status" in obj
        and ("schema_version" in obj or "dispatch_id" in obj)
    )


def build_lane_record(
    *, lane: dict[str, Any], tail_turns: list[dict[str, Any]], root: str
) -> dict[str, Any]:
    closeout: dict[str, Any] | None = None
    best = -1
    for t in tail_turns:
        tn = int(t.get("turn_number") or 0)
        if tn >= best and is_closeout_turn(t):
            best = tn
            closeout = t

    base = {
        "thread": str(lane.get("thread_id") or lane.get("thread") or ""),
        "lane_role": lane.get("lane_role"),
        "status": lane.get("status"),
    }

    if HOP_HARVEST_AVAILABLE and closeout:
        conductor = parse_conductor_closeout(
            body=str(closeout.get("body") or ""),
            dispatch_id="",
            hop_seq=None,
            work_outcome=None,
            degraded_reason=None,
            closeout_turn=int(closeout.get("turn_number") or 0),
            closeout_uri=None,
            usage=None,
            branch=None,
            head_sha=None,
            commits_ahead=None,
        )
        wait = parse_wait_block(closeout_body=str(closeout.get("body") or ""), recon_sidecar_body=None)
        recipe = parse_harvest_recipe(
            worker_thread_id=base["thread"],
            summoning_thread_id=root,
            closeout_turn=int(closeout.get("turn_number") or 0),
            summoning_admit_turn=0,
        )
        view = assemble_operator_hop_view(
            worker_thread={
                "id": base["thread"],
                "status": base["status"],
                "lane_role": base["lane_role"],
            },
            summoning_thread={"id": root},
            conductor=conductor,
            scoreboard={},
            wait=wait,
            job=None,
            harvest_recipe=recipe,
            continuation={},
            next_admit_divergent=False,
        )
        rec = {
            **base,
            "source": "hop_harvest",
            "closeout_turn": view["conductor"]["closeout_turn"],
            "stop_tokens": view["conductor"]["stop_tokens"],
            "next_admit": view["conductor"]["next_admit"],
            "wait_kind": view["wait"]["kind"],
        }
    else:
        tail = []
        for t in sorted(tail_turns, key=lambda x: int(x.get("turn_number") or 0), reverse=True):
            subj = str(t.get("subject") or "")[:80]
            tail.append(
                {
                    "turn": int(t.get("turn_number") or 0),
                    "from": t.get("from") or t.get("from_agent"),
                    "subject": subj,
                }
            )
        rec = {**base, "source": "bus_tail", "tail": tail}

    raw = json.dumps(rec, ensure_ascii=True, separators=(",", ":"))
    if len(raw.encode("utf-8")) <= 400:
        return rec
    rec["truncated"] = True
    if "next_admit" in rec and rec["next_admit"]:
        rec["next_admit"] = str(rec["next_admit"])[:120]
    if rec.get("source") == "bus_tail" and rec.get("tail"):
        while len(json.dumps(rec, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) > 400:
            if not rec["tail"]:
                break
            rec["tail"].pop()
    return rec
