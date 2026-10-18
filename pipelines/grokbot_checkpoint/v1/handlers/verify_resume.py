"""Read-only resume checks. No fs writes and no bus posts."""

from __future__ import annotations

import json
import re
from typing import Any, override

from continuity_tape.messages import seal_messages_sha256
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get, file_sha256, fs_of, step_output_json
from ._handoff import handoff_binding


def _harvest_sha(body: str) -> str | None:
    match = re.search(r"messages_sha256:([0-9a-f]+)", body)
    return match.group(1) if match else None


def _harvest_provenance(body: str) -> str | None:
    match = re.search(r"provenance:([a-z]+)", body)
    return match.group(1) if match else None


_WINDOW_TID = re.compile(r"transcript_id=([0-9a-fA-F-]{8,})")
_WINDOW_TURNS = re.compile(r"turns@cp=(\d+)")


def _seal_rel(session_id: str) -> str:
    return f"notes/system/seals/{session_id}.messages.json"


def _select_checkpoint(
    turns: list,
    *,
    house: str,
    n: object,
    warnings: list[Any],
) -> tuple[str, str | None]:
    checkpoints = [t for t in turns if t.get("subject") == "CHECKPOINT"]
    if house and n is not None and str(n) != "":
        marker = f"house-cp:{house}:{n}"
        matched = [t for t in checkpoints if marker in str(t.get("body") or "")]
        if not matched:
            return "", "verify_resume.house_cp_missing"
        if len(matched) > 1:
            warnings.append("verify_resume.house_cp_ambiguous")
        checkpoints = matched
    if not checkpoints:
        return "", None
    best = max(checkpoints, key=lambda t: int(t.get("turn_number") or 0))
    return str(best.get("body") or ""), None


def _bound_session_id(
    options: dict[str, Any], seal_step: dict[str, Any]
) -> tuple[str, str | None]:
    """Seal step ``session_id`` (seal.py return) wins; options is the fallback."""
    from_step = str(seal_step.get("session_id") or "")
    from_opts = str(options.get("session_id") or "")
    if from_step and from_opts and from_step != from_opts:
        return "", "verify_resume.session_id_mismatch"
    return from_step or from_opts, None


async def run_verify_resume(
    options: dict[str, Any],
    fs: Any,
    *,
    get: Any = None,
    seal_step: dict[str, Any] | None = None,
) -> dict[str, Any]:
    get = get or bus_get
    seal_step = seal_step or {}
    checks: dict[str, Any] = {}
    warnings: list[Any] = []
    session_id, sid_code = _bound_session_id(options, seal_step)
    if sid_code:
        return {
            "refused": None,
            "checks": {"session_id": False},
            "warnings": warnings,
            "writes": 0,
            "posts": 0,
            "code": sid_code,
        }
    card_uri = str(options.get("card_uri") or "")
    packet_sha = str(options.get("expected_card_sha256") or "")
    card_raw = await fs.read(card_uri)
    live = file_sha256(card_raw) if card_raw is not None else ""
    body = card_raw.decode("utf-8") if card_raw else ""
    later = False
    if live != packet_sha and body:
        for line in body.splitlines():
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 4 and cells[3] == packet_sha:
                later = True
    checks["card_sha"] = live == packet_sha or later
    snap_uri = str(options.get("snapshot_uri") or "")
    snap_raw = await fs.read(snap_uri) if snap_uri else None
    snap_live = file_sha256(snap_raw) if snap_raw else ""
    packet_snap = str(options.get("snapshot_sha256") or "")
    checks["snapshot_sha"] = snap_live == packet_snap
    seal_raw = await fs.read(_seal_rel(session_id)) if session_id else None
    if seal_raw is not None:
        doc_raw = seal_raw
        checks["authority"] = "seal"
    else:
        doc_raw = snap_raw
        checks["authority"] = "staging_fallback"
    provenance = ""
    window_tid = ""
    window_count: int | None = None
    turns_known = False
    messages: Any = None
    meta_messages_sha = ""
    parsed: dict[str, Any] = {}
    if doc_raw:
        try:
            parsed = json.loads(doc_raw.decode("utf-8"))
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        meta = parsed.get("meta") if isinstance(parsed, dict) else {}
        if isinstance(meta, dict):
            provenance = str(meta.get("provenance") or "")
            capture = meta.get("capture")
            if isinstance(capture, dict) and str(capture.get("ref") or "").startswith(
                ("cortex://", "workspaces://")
            ):
                cap_raw = await fs.read_uri(str(capture["ref"]))
                checks["capture_sha"] = cap_raw is not None and file_sha256(
                    cap_raw
                ) == capture.get("sha256")
            else:
                checks["capture_sha"] = True
            if meta.get("transcript_id"):
                window_tid = str(meta["transcript_id"])
            if meta.get("turn_count") is not None:
                window_count = int(meta["turn_count"])
                turns_known = True
            if meta.get("messages_sha256"):
                meta_messages_sha = str(meta["messages_sha256"])
        if isinstance(parsed, dict):
            if parsed.get("conversation_uuid"):
                window_tid = str(parsed["conversation_uuid"])
            messages = parsed.get("messages")
    lane = str(options.get("verify_lane") or "")
    root = str(options.get("root_thread") or lane)
    cp_listing, _status = await get("/turns", params={"thread": root})
    receipt_listing, _status = await get("/turns", params={"thread": lane})
    cp_turns = cp_listing.get("turns") if isinstance(cp_listing, dict) else []
    receipt_turns = (
        receipt_listing.get("turns") if isinstance(receipt_listing, dict) else []
    )
    cp_body = ""
    receipt = False
    code: str | None = None
    successor = str(options.get("successor") or options.get("bus_identity") or "")
    fact_turn = int(options.get("fact_turn") or 0)
    house = str(options.get("house") or "")
    n_opt = options.get("n")
    if isinstance(cp_turns, list):
        cp_body, code = _select_checkpoint(
            cp_turns, house=house, n=n_opt, warnings=warnings
        )
    if isinstance(receipt_turns, list):
        for turn in receipt_turns:
            num = int(turn.get("turn_number") or 0)
            if (
                turn.get("subject") == "RECEIPT"
                and str(turn.get("from") or "") == successor
                and num > fact_turn
            ):
                receipt = True
    harvest = _harvest_sha(cp_body)
    file_sha = file_sha256(doc_raw) if doc_raw else ""
    checks["authority_file_sha256"] = file_sha
    if checks.get("authority") == "seal" and isinstance(messages, list):
        recomputed = seal_messages_sha256(messages)
        meta_ok = not meta_messages_sha or meta_messages_sha == recomputed
        checks["authority_sha"] = bool(recomputed) and harvest == recomputed and meta_ok
    else:
        checks["authority_sha"] = bool(file_sha) and harvest == file_sha
    checks["harvest_sha"] = checks["authority_sha"]
    tip_prov = _harvest_provenance(cp_body)
    if tip_prov is None:
        checks["provenance"] = True
        warnings.append("provenance: unknown")
    else:
        checks["provenance"] = bool(provenance) and tip_prov == provenance
    if window_tid:
        found = _WINDOW_TID.search(cp_body)
        checks["window_transcript"] = bool(found) and found.group(1) == window_tid
    if turns_known and window_count is not None:
        found_turns = _WINDOW_TURNS.search(cp_body)
        checks["window_turns"] = (
            bool(found_turns) and int(found_turns.group(1)) == window_count
        )
    elif checks.get("authority") == "seal":
        checks["window_turns"] = "unknown"
        warnings.append("window_turns: turn_count absent")
    checks["receipt"] = receipt
    raw_letter = parsed.get("handoff") if isinstance(parsed, dict) else None
    if "handoff" in parsed and not isinstance(raw_letter, dict):
        checks["handoff"] = "stale"
        checks["handoff_reasons"] = ["shape"]
    else:
        letter = raw_letter if isinstance(raw_letter, dict) else None
        n_bind = n_opt if isinstance(n_opt, int) else 0
        binding, reasons = handoff_binding(
            letter,
            messages=messages if isinstance(messages, list) else [],
            messages_sha256=meta_messages_sha,
            house=house,
            n=n_bind,
        )
        checks["handoff"] = binding
        if reasons:
            checks["handoff_reasons"] = reasons
        if binding == "missing":
            warnings.append(
                {
                    "code": "verify_resume.handoff_missing",
                    "message": "authority document has no handoff letter",
                }
            )
    payload: dict[str, Any] = {
        "refused": None,
        "checks": checks,
        "warnings": warnings,
        "writes": 0,
        "posts": 0,
    }
    if code:
        payload["code"] = code
        checks["house_cp"] = False
    return payload


class GrokbotCheckpointVerifyResumeHandler(BaseHandler):
    step_type = "grokbot_checkpoint_verify_resume_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        outputs = getattr(context, "outputs", {}) or {}
        seal_step = step_output_json(outputs, "seal")
        if options.get("mode") == "harvest_seal":
            envelope = step_output_json(outputs, "envelope")
            post = step_output_json(outputs, "post")
            fact = step_output_json(outputs, "fact")
            merged = dict(options)
            if envelope.get("snapshot_uri") and not merged.get("snapshot_uri"):
                merged["snapshot_uri"] = envelope["snapshot_uri"]
            if envelope.get("sha256") and not merged.get("snapshot_sha256"):
                merged["snapshot_sha256"] = envelope["sha256"]
            if post.get("turn") is not None:
                merged.setdefault("cp_turn_hint", post["turn"])
            if fact.get("turn") is not None:
                merged["fact_turn"] = fact["turn"]
            options = merged
        payload = await run_verify_resume(options, fs_of(context), seal_step=seal_step)
        return StepOutput(raw="", json=payload)
