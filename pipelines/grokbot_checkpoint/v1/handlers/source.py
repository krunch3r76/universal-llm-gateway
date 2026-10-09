"""Read a poured envelope and validate the pour contract."""

from __future__ import annotations

import json
from typing import Any, override

from continuity_tape.pour_contract import PourRefused, validate_pour
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import FsClientMissingError, file_sha256, fs_of, refuse, rel_path_ok


async def _load_slot(
    fs: Any, ref: str, expected: str, slot: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if ref.startswith(("cortex://", "workspaces://")):
        reader = getattr(fs, "read_uri", None)
        raw = await reader(ref) if reader is not None else await fs.read(ref)
    elif not rel_path_ok(ref):
        return None, refuse("admit.path_escape", f"refused path {ref}")
    else:
        raw = await fs.read(ref)
    if raw is None:
        return None, refuse("source.missing", f"envelope {ref} absent")
    digest = file_sha256(raw)
    if digest != expected:
        return None, refuse("source.sha_mismatch", f"{digest} != {expected}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return None, refuse("pour.schema", str(exc))
    if not isinstance(payload, dict):
        return None, refuse("pour.schema", "envelope is not an object")
    try:
        envelope = validate_pour(payload)
    except PourRefused as exc:
        return None, refuse(exc.code, exc.message)
    if envelope.meta.provenance != slot:
        return None, refuse(
            "source.slot_mismatch",
            f"slot {slot} got provenance {envelope.meta.provenance}",
        )
    producer = (envelope.meta.sources or [{}])[0].get("producer") or {}
    return {
        "envelope": payload,
        "provenance": envelope.meta.provenance,
        "producer": producer.get("id"),
        "coverage": envelope.meta.coverage,
        "messages_sha256": envelope.meta.messages_sha256,
        "surface": envelope.meta.surface,
        "transcript_id": envelope.meta.transcript_id,
        "turn_count": envelope.meta.turn_count,
        "sha256": digest,
        "ref": ref,
        "capture_meta": (
            envelope.meta.capture.model_dump() if envelope.meta.capture else None
        ),
    }, None


async def _read_capture(fs: Any, ref: str) -> bytes | None:
    text = ref.strip()
    for prefix in ("cortex://", "workspaces://"):
        if text.startswith(prefix):
            reader = getattr(fs, "read_uri", None)
            if reader is not None:
                return await reader(text)
            return await fs.read(text.removeprefix(prefix))
    return None


async def _attach_capture(selected: dict[str, Any], fs: Any) -> dict[str, Any]:
    capture = selected.get("capture_meta")
    if not capture:
        selected["capture"] = "none"
        selected["capture_verified"] = None
        return selected
    ref = str(capture.get("ref") or "")
    if not ref.startswith(("cortex://", "workspaces://")):
        return refuse("capture.unreadable", f"capture ref {ref}")
    raw = await _read_capture(fs, ref)
    if raw is None:
        return refuse("capture.unreadable", f"capture ref {ref} unreadable")
    digest = file_sha256(raw)
    if digest != capture.get("sha256"):
        return refuse("capture.sha_mismatch", f"{digest} != {capture.get('sha256')}")
    selected["capture"] = {**capture, "verified": True}
    selected["capture_verified"] = True
    return selected


async def run_source(options: dict[str, Any], fs: Any) -> dict[str, Any]:
    slots: list[tuple[str, str, str]] = []
    transcribed_ref = str(options.get("transcribed_ref") or "")
    raw_ref = str(options.get("raw_ref") or "")
    if transcribed_ref:
        slots.append(
            ("transcribed", transcribed_ref, str(options.get("transcribed_sha256") or ""))
        )
    if raw_ref:
        slots.append(("raw", raw_ref, str(options.get("raw_sha256") or "")))
    if not slots:
        return refuse("source.no_pour", "seal mode needs a transcribed or raw pour")
    loaded: dict[str, dict[str, Any]] = {}
    first_refusal: dict[str, Any] | None = None
    try:
        loaded_slots = []
        for slot, ref, expected in slots:
            loaded_slots.append((slot, ref, expected, await _load_slot(fs, ref, expected, slot)))
    except FsClientMissingError as exc:
        return refuse(exc.code, str(exc))
    for slot, ref, expected, (body, err) in loaded_slots:
        if err:
            first_refusal = first_refusal or err
            continue
        assert body is not None
        loaded[slot] = body
    root_thread = str(options.get("root_thread") or "").strip()
    agent_id = str(options.get("agent_id") or "").strip()
    if root_thread and agent_id:
        for slot_name in ("transcribed", "raw"):
            body = loaded.get(slot_name)
            if body is not None and body.get("transcript_id") != agent_id:
                return refuse(
                    "admit.transcript_id_mismatch",
                    f"{slot_name} pour meta.transcript_id "
                    f"{body.get('transcript_id')!r} != agent_id {agent_id!r}",
                )
    selected: dict[str, Any] | None = None
    alternate = None
    fallback = None
    if "transcribed" in loaded:
        selected = loaded["transcribed"]
        if "raw" in loaded:
            raw = loaded["raw"]
            alternate = {
                "ref": raw["ref"],
                "sha256": raw["sha256"],
                "messages_sha256": raw["messages_sha256"],
            }
        elif first_refusal and any(slot == "raw" for slot, _ref, _sha in slots):
            selected["alternate_refused"] = (first_refusal.get("refused") or {})
    elif "raw" in loaded and any(slot == "transcribed" for slot, _ref, _sha in slots):
        selected = loaded["raw"]
        fallback = {
            "from": "transcribed",
            "code": (first_refusal or {}).get("refused", {}).get("code"),
        }
    elif "raw" in loaded:
        selected = loaded["raw"]
    if selected is None:
        return first_refusal or refuse("source.no_pour", "no valid pour")
    checked = await _attach_capture(selected, fs)
    if checked.get("refused"):
        return checked
    checked["alternate"] = alternate
    checked["fallback"] = fallback
    checked["refused"] = None
    return checked


class GrokbotCheckpointSourceHandler(BaseHandler):
    step_type = "grokbot_checkpoint_source_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        payload = await run_source(options, fs_of(context))
        return StepOutput(raw="", json=payload)
