"""Shared harvest_seal helpers (adapter runs, record line, pour staging)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import continuity_tape.extract_grokbot_jsonl as _adapter_mod
from continuity_tape.extract_grokbot_jsonl import (
    TranscriptPageError,
    extract_grokbot_transcript,
)
from continuity_tape.messages import envelope_wire_dict
from continuity_tape.pour_contract import PourRefused, validate_pour

from ._clients import dumps, file_sha256, refuse


def adapter_read_sha256() -> str:
    data = Path(_adapter_mod.__file__).read_bytes()
    return hashlib.sha256(data).hexdigest()


def checkpoint_raw_uri(agent_id: str, n: int) -> str:
    return f"notes/system/checkpoints/grok/{agent_id}/{n}.raw.json"


def checkpoint_messages_uri(agent_id: str, n: int) -> str:
    return f"notes/system/checkpoints/grok/{agent_id}/{n}.messages.json"


def run_adapter_twice(
    text: str,
    *,
    agent_id: str,
    capture_ref: str,
    capture_sha256: str,
    bus_identity: str | None,
    capture_kind: str = "grok_export",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run raw transcript extraction twice; return (envelope_dict, error)."""
    if "tape/" in capture_ref.replace("\\", "/"):
        return None, refuse("harvest.tape_refused", f"tape path {capture_ref}")
    kwargs: dict[str, Any] = {
        "agent_id": agent_id,
        "capture_ref": capture_ref,
        "capture_sha256": capture_sha256,
        "capture_kind": capture_kind,
    }
    if bus_identity:
        kwargs["bus_identity"] = bus_identity
    # Checkpoint pours store normalized page bytes; dom_harvest matches validate_pour.
    kwargs["capture_kind"] = "dom_harvest"
    try:
        actual_text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    except (ValueError, UnicodeDecodeError) as exc:
        return None, refuse("pour.schema", str(exc))
    if capture_sha256 != actual_text_sha256:
        return None, refuse(
            "source.sha_mismatch",
            f"capture_sha256 {capture_sha256} != normalized {actual_text_sha256}",
        )
    try:
        first = extract_grokbot_transcript(text, observed_at="harvest-run-1", **kwargs)
        second = extract_grokbot_transcript(text, observed_at="harvest-run-2", **kwargs)
    except TranscriptPageError as exc:
        return None, refuse("pour.schema", str(exc))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, refuse("pour.schema", str(exc))
    m1 = first.meta.messages_sha256
    m2 = second.meta.messages_sha256
    if m1 != m2:
        return None, refuse("source.sha_mismatch", f"adapter runs differ {m1} != {m2}")
    if first.meta.coverage != "full":
        return None, refuse("pour.coverage", f"coverage {first.meta.coverage!r}")
    if first.meta.provenance != "raw":
        return None, refuse(
            "source.slot_mismatch", f"provenance {first.meta.provenance!r}"
        )
    window = (first.meta.sources or [{}])[0].get("window") or {}
    lo, hi, total = window.get("lo"), window.get("hi"), window.get("total")
    if lo != 1 or hi != total:
        return None, refuse("pour.window", f"window lo={lo} hi={hi} total={total}")
    cap = first.meta.capture
    cap_sha = cap.sha256 if cap is not None else None
    if cap_sha != actual_text_sha256:
        return None, refuse(
            "source.sha_mismatch",
            f"capture.sha256 {cap_sha} != normalized {actual_text_sha256}",
        )
    payload = envelope_wire_dict(first)
    try:
        validate_pour(payload)
    except PourRefused as exc:
        return None, refuse(exc.code, exc.message)
    stats = {
        "messages_sha256": m1,
        "source_sha256": first.meta.source_sha256,
        "coverage": first.meta.coverage,
        "truncated_parts": (first.meta.sources or [{}])[0].get("truncated_parts", 0),
        "turn_count": first.meta.turn_count,
        "adapter_read_sha256": adapter_read_sha256(),
    }
    return {"envelope": payload, "stats": stats}, None


def grok_pour_uri(agent_id: str, n: int = 1) -> str:
    return checkpoint_messages_uri(agent_id, n)


async def stage_grok_pour(
    fs: Any,
    agent_id: str,
    envelope: dict[str, Any],
    *,
    stats: dict[str, Any],
    n: int = 1,
    raw_bytes: bytes | None = None,
    l0_ref: str = "",
) -> dict[str, Any]:
    """Write the verbatim L0 bytes and the raw messages envelope."""
    from cortex_store.verbatim_succession import prefix_holds

    uri = grok_pour_uri(agent_id, n)
    if "tape/" in uri:
        return refuse("harvest.tape_refused", uri)
    if raw_bytes is not None:
        raw_uri = checkpoint_raw_uri(agent_id, n)
        raw_written = await fs.write(raw_uri, raw_bytes, if_absent=True)
        if not raw_written.get("ok"):
            existing_raw = await fs.read(raw_uri)
            if existing_raw != raw_bytes:
                return refuse("harvest.raw_conflict", "raw checkpoint bytes differ")
    raw = dumps(envelope)
    existing = await fs.read(uri)
    if existing is None:
        written = await fs.write(uri, raw, if_absent=True)
        if not written.get("ok"):
            live = await fs.read(uri)
            if live is None:
                return refuse("pour.schema", "pour if_absent lost the race")
            existing = live
        else:
            read_back = await fs.read(uri)
            if read_back is None or file_sha256(read_back) != written["sha256"]:
                return refuse("source.sha_mismatch", "pour read-back sha mismatch")
            return _pour_source(uri, envelope, written["sha256"], stats)
    live_sha = file_sha256(existing)
    try:
        old = json.loads(existing.decode("utf-8"))
    except json.JSONDecodeError:
        return refuse("pour.schema", "existing pour is not json")
    old_msgs = old.get("messages") if isinstance(old, dict) else None
    new_msgs = envelope.get("messages")
    old_msha = (old.get("meta") or {}).get("messages_sha256")
    new_msha = stats.get("messages_sha256")
    if old_msha == new_msha:
        if old_msgs != new_msgs:
            return refuse("envelope.not_prefix", "messages_sha256 match but messages differ")
        read_back = await fs.read(uri)
        if read_back is None or file_sha256(read_back) != live_sha:
            return refuse("source.sha_mismatch", "pour read-back sha mismatch")
        return _pour_source(uri, envelope, live_sha, stats)
    if not isinstance(old_msgs, list) or not isinstance(new_msgs, list):
        return refuse("envelope.not_prefix", "messages missing")
    if not prefix_holds("messages-v1", old_msgs, new_msgs):
        return refuse("envelope.not_prefix", "new messages do not prefix-extend")
    written = await fs.write(uri, raw, expected_sha256=live_sha)
    if not written.get("ok"):
        return refuse("envelope.not_prefix", "guarded pour overwrite lost the race")
    read_back = await fs.read(uri)
    if read_back is None or file_sha256(read_back) != written["sha256"]:
        return refuse("source.sha_mismatch", "pour read-back sha mismatch")
    return _pour_source(uri, envelope, written["sha256"], stats)


def _pour_source(
    uri: str, envelope: dict[str, Any], sha256: str, stats: dict[str, Any]
) -> dict[str, Any]:
    meta = envelope.get("meta") if isinstance(envelope.get("meta"), dict) else {}
    capture = meta.get("capture")
    producer = (meta.get("sources") or [{}])[0].get("producer") or {}
    return {
        "envelope": envelope,
        "provenance": meta.get("provenance"),
        "producer": producer.get("id"),
        "coverage": meta.get("coverage"),
        "messages_sha256": stats.get("messages_sha256"),
        "surface": meta.get("surface"),
        "transcript_id": meta.get("transcript_id"),
        "turn_count": stats.get("turn_count"),
        "sha256": sha256,
        "ref": uri,
        "capture_meta": capture,
        "capture": {**capture, "verified": True} if isinstance(capture, dict) else "none",
        "capture_verified": True if isinstance(capture, dict) else None,
        "alternate": None,
        "fallback": None,
        "source_sha256": stats.get("source_sha256"),
        "truncated_parts": stats.get("truncated_parts", 0),
        "adapter_read_sha256": stats.get("adapter_read_sha256"),
        "refused": None,
    }


def format_harvest_record(
    *,
    l0_ref: str,
    l0_sha256: str,
    pages: int,
    l0_bytes: int,
    truncated_parts: int,
    l1_ref: str,
    l1_sha256: str,
    messages_sha256: str,
    source_sha256: str,
    adapter_read_sha256: str,
    capture_sha256: str,
    capture_kind: str = "grok_export",
    session_id: str | None = None,
    cp_turn: Any = None,
    fact_turn: Any = None,
    verify_resume_ok: bool | None = None,
) -> str:
    cap12 = capture_sha256[:12]
    line = (
        f"raw {l0_ref}@{l0_sha256} pages={pages} bytes={l0_bytes} "
        f"truncated_parts={truncated_parts} · "
        f"messages {l1_ref}@{l1_sha256} messages_sha256={messages_sha256} "
        f"source_sha256={source_sha256} coverage=full · "
        f"adapter {adapter_read_sha256} · "
        f"capture:{capture_kind}@{cap12}"
        + ("~pseudo" if capture_kind == "dom_harvest" else "")
    )
    if session_id:
        vr = verify_resume_ok if verify_resume_ok is not None else False
        line += (
            f" · seal {session_id} cp_turn={cp_turn} fact_turn={fact_turn} "
            f"verify_resume={vr}"
        )
    return line


__all__ = [
    "adapter_read_sha256",
    "format_harvest_record",
    "checkpoint_messages_uri",
    "checkpoint_raw_uri",
    "grok_pour_uri",
    "run_adapter_twice",
    "stage_grok_pour",
]
