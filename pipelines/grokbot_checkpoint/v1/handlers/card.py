"""Append one checkpoint-log row when the live card sha matches."""

from __future__ import annotations

from datetime import datetime
from typing import Any, override
from zoneinfo import ZoneInfo

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import file_sha256, fs_of, refuse, rel_path_ok, step_output_json

_PT = ZoneInfo("America/Los_Angeles")


def _last_row_n(body: str) -> int | None:
    rows = [
        line
        for line in body.splitlines()
        if line.startswith("|") and not line.startswith("| n") and "---" not in line
    ]
    if not rows:
        return None
    cells = [c.strip() for c in rows[-1].strip("|").split("|")]
    if not cells or not cells[0].isdigit():
        return None
    return int(cells[0])


def _log_header_ok(body: str, n: int) -> bool:
    if "## Checkpoint log" not in body:
        return False
    last = _last_row_n(body)
    if n == 1:
        return last is None
    return last == n - 1


async def run_card(
    options: dict[str, Any],
    source: dict[str, Any],
    envelope_step: dict[str, Any],
    fs: Any,
    *,
    execution_id: str,
    seal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if seal and seal.get("refused"):
        return {"refused": seal["refused"]}
    uri = str(options.get("card_uri") or "")
    if not rel_path_ok(uri):
        return refuse("admit.path_escape", f"refused path {uri}")
    expected = str(options.get("expected_card_sha256") or "")
    raw = fs.read(uri)
    if raw is None:
        return refuse("card.missing", f"{uri} absent")
    live = file_sha256(raw)
    body = raw.decode("utf-8")
    n = int(options["n"])
    if live != expected:
        if _last_row_n(body) == n:
            return {
                "refused": None,
                "replayed": True,
                "uri": uri,
                "prior_sha256": live,
                "written_sha256": live,
            }
        return refuse("card.sha_mismatch", f"live {live} != expected {expected}")
    mode = str(options.get("mode") or "")
    if not _log_header_ok(body, n):
        return refuse("card.body_invalid", "checkpoint log last row is not cp n-1")
    when = datetime.now(_PT).strftime("%Y-%m-%d %H:%M %Z")
    trigger = str(options.get("trigger") or "")
    if mode == "light":
        cell = "none"
    else:
        snap = str(envelope_step.get("snapshot_uri") or "")
        snap_sha = str(envelope_step.get("sha256") or "")
        msg = str(envelope_step.get("messages_sha256") or "")
        prov = str(envelope_step.get("provenance") or source.get("provenance") or "")
        prod = str(envelope_step.get("producer") or source.get("producer") or "")
        cap = envelope_step.get("capture") or source.get("capture")
        if isinstance(cap, dict):
            short = str(cap.get("sha256") or "")[:12]
            pseudo = "~pseudo" if cap.get("pseudo_byte_exact") else ""
            cap_tok = f"{cap.get('kind')}@{short}{pseudo}"
        else:
            cap_tok = "none"
        cell = (
            f"{snap}@{snap_sha} · {msg} · provenance:{prov} · producer:{prod} · "
            f"capture:{cap_tok}"
        )
    row = (
        f"| {n} | {when} | {trigger} | {expected} | {cell} | pipeline {execution_id} |"
    )
    updated = body.rstrip() + "\n" + row + "\n"
    data = updated.encode("utf-8")
    written = fs.write(uri, data, expected_sha256=expected)
    if not written.get("ok"):
        return refuse("card.conflict", "409 card write")
    return {
        "refused": None,
        "replayed": False,
        "uri": uri,
        "prior_sha256": expected,
        "written_sha256": written["sha256"],
        "row": row,
    }


class GrokbotCheckpointCardHandler(BaseHandler):
    step_type = "grokbot_checkpoint_card_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        outputs = getattr(context, "outputs", {}) or {}
        payload = await run_card(
            options,
            step_output_json(outputs, "source"),
            step_output_json(outputs, "envelope"),
            fs_of(context),
            execution_id=str(getattr(context, "execution_id", "") or ""),
            seal=step_output_json(outputs, "seal"),
        )
        return StepOutput(raw="", json=payload)
