"""Merge sections, caps, first_act, reinduct, packet serialization."""

from __future__ import annotations

import copy
import json
from typing import Any

ALLOWED_LIVE_ERROR_KINDS = ("scoreboard_missing", "gate_unparsed")
TOP_LEVEL_KEYS = (
    "house",
    "checkpoint",
    "continuity",
    "journal",
    "runbook",
    "scores",
    "lanes",
    "open_consults",
    "open_consults_outside_house",
    "first_act",
    "reinduct",
    "meta",
)

CAPS = {
    "house": 2000,
    "checkpoint": 2500,
    "continuity": 1800,
    "journal": 2400,
    "runbook": 1000,
    "scores": 3000,
    "lanes": 2500,
}


def dumps_packet(packet: dict[str, Any]) -> str:
    return json.dumps(packet, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def packet_nbytes(packet: dict[str, Any]) -> int:
    return len(dumps_packet(packet).encode("utf-8"))


def compute_first_act(
    *,
    open_consults: list[dict[str, Any]],
    continuity: dict[str, Any],
    checkpoint: dict[str, Any],
) -> tuple[str | None, str]:
    if open_consults:
        oldest = open_consults[-1]
        prefix = oldest.get("subject_prefix") or "HARVEST — G?"
        act = f"HARVEST {oldest.get('thread')}#{oldest.get('turn')} — {prefix}"
        return act[:200], "consult"
    if isinstance(continuity, dict) and "error" not in continuity:
        np = continuity.get("next_pickup") or []
        if np:
            return str(np[0])[:200], "continuity"
    if isinstance(checkpoint, dict) and "error" not in checkpoint:
        for step in checkpoint.get("steps") or []:
            if isinstance(step, dict) and step.get("mark") == " ":
                return str(step.get("text") or "")[:200], "checkpoint_step"
    return None, "none"


def build_reinduct(*, root: str, options: dict[str, Any]) -> dict[str, Any]:
    rule = (
        f"`MAESTRO {root}` in context ⇒ the seat calls the pipeline first, "
        "before any other tool."
    )
    return {
        "call": {
            "op": "run",
            "pipeline_id": "maestro-induct",
            "messages": [{"role": "user", "content": f"MAESTRO {root}"}],
            "options": {
                "root": root,
                "journal_entries": options.get("journal_entries", 2),
                "include_runbook": options.get("include_runbook", True),
                "include_lanes": options.get("include_lanes", True),
            },
        },
        "trigger": {"token": f"MAESTRO {root}", "rule": rule},
    }


def reserve_stamps(packet: dict[str, Any]) -> None:
    meta = packet.setdefault("meta", {})
    meta["generated_at"] = "1970-01-01T00:00:00Z"
    meta["latency_ms"] = 99999999


def _trim_house(house: dict[str, Any], cap: int) -> tuple[dict[str, Any], int]:
    headings = copy.deepcopy(house.get("headings") or [])
    protected = {"Objective", "Runbooks"}
    dropped = 0
    while headings and _section_bytes("house", {"headings": headings, **{k: house.get(k) for k in house if k != "headings"}}) > cap:
        for i in range(len(headings) - 1, -1, -1):
            if headings[i].get("title") not in protected:
                headings.pop(i)
                dropped += 1
                break
        else:
            for h in headings:
                if h.get("title") not in protected and h.get("body"):
                    h["body"] = h["body"][:200]
                    dropped += 1
                    break
            else:
                break
    out = dict(house)
    out["headings"] = headings
    return out, dropped


def _trim_checkpoint(cp: dict[str, Any], cap: int) -> tuple[dict[str, Any], int]:
    out = copy.deepcopy(cp)
    dropped = 0
    while packet_nbytes({"checkpoint": out}) > cap:
        if out.get("other_headings"):
            out["other_headings"] = out["other_headings"][:-1]
            dropped += 1
            continue
        if out.get("state") and len(out["state"]) > 200:
            out["state"] = out["state"][:200]
            dropped += 1
            continue
        steps = out.get("steps") or []
        closed = [s for s in steps if s.get("mark") == "x"]
        if closed:
            out["steps"] = [s for s in steps if s.get("mark") != "x" or s is not closed[0]]
            out["steps"] = steps[1:] if steps and steps[0].get("mark") == "x" else steps
            dropped += 1
            continue
        if out.get("anchor") and len(out["anchor"]) > 200:
            out["anchor"] = out["anchor"][:200]
            dropped += 1
            continue
        break
    return out, dropped


def _section_bytes(key: str, val: Any) -> int:
    return len(json.dumps({key: val}, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _trim_continuity(cont: dict[str, Any], cap: int) -> dict[str, Any]:
    out = copy.deepcopy(cont)
    if _section_bytes("continuity", out) <= cap:
        return out
    if out.get("settled"):
        out["settled"] = None
    leg = out.get("latest_leg")
    if isinstance(leg, dict) and leg.get("body"):
        leg["body"] = leg["body"][:400]
    np = out.get("next_pickup") or []
    while len(np) > 1 and _section_bytes("continuity", out) > cap:
        np.pop()
    out["next_pickup"] = np
    if out.get("live") and _section_bytes("continuity", out) > cap:
        out["live"] = out["live"][:400]
    return out


def apply_size_caps(packet: dict[str, Any]) -> None:
    meta = packet.setdefault("meta", {})
    truncated = meta.setdefault("truncated", {})
    order = ["lanes", "scores", "continuity", "checkpoint", "runbook", "journal", "house"]
    for key in order:
        if packet_nbytes(packet) <= 12288:
            return
        cap = CAPS.get(key)
        if cap is None or key not in packet:
            continue
        val = packet.get(key)
        if val is None or (isinstance(val, dict) and val.get("dropped")):
            continue
        if key == "house" and isinstance(val, dict):
            packet["house"], n = _trim_house(val, cap)
            if n:
                truncated["house"] = truncated.get("house", 0) + n
        elif key == "continuity" and isinstance(val, dict) and "error" not in val:
            packet["continuity"] = _trim_continuity(val, cap)
            if _section_bytes("continuity", packet["continuity"]) >= cap:
                truncated["continuity"] = 1
        elif key == "checkpoint" and isinstance(val, dict) and "error" not in val:
            packet["checkpoint"], n = _trim_checkpoint(val, cap)
            if n:
                truncated["checkpoint"] = n
        elif key == "lanes" and isinstance(val, list):
            dropped = 0
            while val and packet_nbytes(packet) > 12288:
                val.pop()
                dropped += 1
            if dropped:
                truncated["lanes"] = dropped
        elif key == "journal" and isinstance(val, dict):
            entries = list(val.get("entries") or [])
            dropped = 0
            while len(entries) > 1 and packet_nbytes(packet) > 12288:
                entries.pop()
                dropped += 1
            packet["journal"] = {"entries": entries}
            if dropped:
                truncated["journal"] = dropped
        elif key == "runbook" and isinstance(val, dict):
            steps = list(val.get("steps") or [])
            while len(steps) > 1 and _section_bytes("runbook", {"steps": steps}) > cap:
                steps.pop()
            val["steps"] = steps
            packet["runbook"] = val
    if packet_nbytes(packet) > 12288:
        _terminal_shrink(packet)


def _terminal_shrink(packet: dict[str, Any]) -> None:
    meta = packet.setdefault("meta", {})
    truncated = meta.setdefault("truncated", {})
    consults = packet.get("open_consults") or []
    recipe_only = []
    for c in consults:
        recipe_only.append(
            {
                "thread": c.get("thread"),
                "turn": c.get("turn"),
                "after_turn": c.get("after_turn"),
                "subject_prefix": c.get("subject_prefix"),
            }
        )
    packet["open_consults"] = recipe_only
    truncated["open_consults"] = "recipe_only"
    if packet_nbytes(packet) > 12288:
        for k in list(packet.keys()):
            if k in ("first_act", "reinduct", "meta", "open_consults"):
                continue
            packet[k] = {"dropped": True}
        meta["oversize"] = True
        ids = [f"{c.get('thread')}#{c.get('turn')}" for c in recipe_only]
        while ids and packet_nbytes(packet) > 12288:
            ids.pop(0)
        packet["open_consults"] = ids
        truncated["open_consults_ids"] = len(recipe_only) - len(ids)


def merge_sections(sections: dict[str, Any], *, root: str, options: dict[str, Any]) -> dict[str, Any]:
    resolve = sections.get("resolve") or {}
    skipped: list[str] = list(resolve.get("skipped") or [])
    meta: dict[str, Any] = {
        "root": root,
        "started_at": resolve.get("started_at"),
        "generated_at": None,
        "card_sha256": (sections.get("fetch_house") or {}).get("card_sha256"),
        "checkpoint_turn": (sections.get("fetch_checkpoint") or {}).get("turn"),
        "lane_ids": (sections.get("enumerate_lanes") or {}).get("lane_ids") or [],
        "skipped": skipped,
        "truncated": dict((sections.get("fetch_consults") or {}).get("truncated_meta") or {}),
        "counts": {},
        "errors": [],
        "latency_ms": None,
    }
    for sec in sections.values():
        if isinstance(sec, dict):
            for err in sec.get("errors") or []:
                meta["errors"].append(err)
    packet: dict[str, Any] = {
        "house": sections.get("fetch_house"),
        "checkpoint": sections.get("fetch_checkpoint"),
        "continuity": sections.get("fetch_continuity"),
        "journal": sections.get("fetch_journal"),
        "runbook": sections.get("fetch_runbook"),
        "scores": (sections.get("fetch_scores") or {}).get("scores") or [],
        "lanes": (sections.get("fetch_lanes") or {}).get("lanes") or [],
        "open_consults": (sections.get("fetch_consults") or {}).get("open_consults") or [],
        "open_consults_outside_house": (sections.get("fetch_consults") or {}).get(
            "open_consults_outside_house"
        )
        or [],
        "first_act": None,
        "reinduct": None,
        "meta": meta,
    }
    if "runbook" in skipped:
        packet["runbook"] = None
    if "lanes" in skipped:
        packet["lanes"] = []
    return packet


def assemble_packet(*, sections: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    root = str(options.get("root") or sections.get("resolve", {}).get("root") or "")
    packet = merge_sections(sections, root=root, options=options)
    first, src = compute_first_act(
        open_consults=packet.get("open_consults") or [],
        continuity=packet.get("continuity") or {},
        checkpoint=packet.get("checkpoint") or {},
    )
    packet["first_act"] = first
    packet["meta"]["counts"]["first_act_source"] = src
    packet["reinduct"] = build_reinduct(root=root, options=options)
    reserve_stamps(packet)
    apply_size_caps(packet)
    return packet


def check_packet_sections(
    packet: dict[str, Any], *, allowed_kinds: tuple[str, ...]
) -> list[str]:
    violations: list[str] = []
    skipped = set((packet.get("meta") or {}).get("skipped") or [])
    for key in TOP_LEVEL_KEYS:
        if key not in packet:
            violations.append(f"missing key {key}")
    for key in TOP_LEVEL_KEYS:
        val = packet.get(key)
        if key in skipped or val is None:
            continue
        if isinstance(val, dict) and "error" in val:
            violations.append(f"section {key} error envelope")
    meta = packet.get("meta") or {}
    for err in meta.get("errors") or []:
        kind = err.get("kind") if isinstance(err, dict) else None
        if kind and kind not in allowed_kinds:
            violations.append(f"meta.errors kind {kind} not allowed")
    if meta.get("checkpoint_turn") is None:
        violations.append("meta.checkpoint_turn null")
    if meta.get("card_sha256") is None:
        violations.append("meta.card_sha256 null")
    scores = packet.get("scores") or []
    if scores and all(isinstance(s, dict) and "error" in s for s in scores):
        violations.append("all scores errored")
    return violations
