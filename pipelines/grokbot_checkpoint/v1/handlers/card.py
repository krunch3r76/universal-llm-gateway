"""Append one checkpoint-log row when the live card sha matches."""

from __future__ import annotations

from datetime import datetime
from typing import Any, override
from zoneinfo import ZoneInfo

from agent_bus_store.house_pools import _continuity_card_candidate_relpaths
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import file_sha256, fs_of, refuse, rel_path_ok, step_output_json

_PT = ZoneInfo("America/Los_Angeles")


def card_binding_warnings(options: dict[str, Any]) -> list[dict[str, str]]:
    root_thread = str(options.get("root_thread") or "").strip()
    if not root_thread:
        return []
    card_uri = str(options.get("card_uri") or "")
    rel = card_uri.removeprefix("cortex://")
    root = root_thread.removeprefix("agent-bus:")
    ladder = list(_continuity_card_candidate_relpaths(root))
    if rel == ladder[0] or rel in ladder[1:]:
        return []
    return [
        {
            "code": "card.resume_unbound",
            "message": f"{rel} is not on the resume card ladder for {root}",
        }
    ]


def card_binding_notices(options: dict[str, Any]) -> list[dict[str, str]]:
    """Info when the card is on the resume ladder but is not the canonical name."""
    root_thread = str(options.get("root_thread") or "").strip()
    if not root_thread:
        return []
    card_uri = str(options.get("card_uri") or "")
    rel = card_uri.removeprefix("cortex://")
    root = root_thread.removeprefix("agent-bus:")
    ladder = list(_continuity_card_candidate_relpaths(root))
    if not ladder or rel == ladder[0] or rel not in ladder[1:]:
        return []
    return [
        {
            "code": "card.legacy_name",
            "message": f"{rel} resolves on resume but is not {root}-card.md",
        }
    ]


def _log_section(body: str) -> str:
    if "## Checkpoint log" not in body:
        return ""
    rest = body.split("## Checkpoint log", 1)[1]
    nxt = rest.find("\n## ")
    return rest if nxt < 0 else rest[:nxt]


def _last_row_n(body: str) -> int | None:
    rows = [
        line
        for line in _log_section(body).splitlines()
        if line.startswith("|") and not line.startswith("| n") and "---" not in line
    ]
    if not rows:
        return None
    cells = [c.strip() for c in rows[-1].strip("|").split("|")]
    if not cells or not cells[0].isdigit():
        return None
    return int(cells[0])


_LOG_HEADER = (
    "\n## Checkpoint log\n\n"
    "| n | when | trigger | prior card sha | seal | by |\n"
    "|---|------|---------|----------------|------|----|\n"
)


def _with_log_header(body: str, n: int) -> str:
    """Append the log header on the first checkpoint when the section is absent."""
    if "## Checkpoint log" in body or n != 1:
        return body
    return body.rstrip("\n") + "\n" + _LOG_HEADER


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
    if options.get("mode") == "seal" and (
        not seal
        or seal.get("_skipped")
        or seal.get("refused")
        or not seal.get("session_id")
    ):
        return refuse("card.upstream_skipped", "seal did not yield a session_id")
    if seal and seal.get("refused"):
        return {"refused": seal["refused"]}
    uri = str(options.get("card_uri") or "")
    if not rel_path_ok(uri):
        return refuse("admit.path_escape", f"refused path {uri}")
    expected = str(options.get("expected_card_sha256") or "")
    raw = await fs.read(uri)
    if raw is None:
        return refuse("card.missing", f"{uri} absent")
    live = file_sha256(raw)
    supplied = options.get("card_body")
    if isinstance(supplied, str) and supplied:
        body = supplied
    else:
        body = raw.decode("utf-8")
    n = int(options["n"])
    warnings = card_binding_warnings(options)
    notices = card_binding_notices(options)
    if live != expected:
        if _last_row_n(raw.decode("utf-8")) == n:
            return {
                "refused": None,
                "replayed": True,
                "uri": uri,
                "prior_sha256": live,
                "written_sha256": live,
                "warnings": warnings,
                "notices": notices,
            }
        return refuse("card.sha_mismatch", f"live {live} != expected {expected}")
    mode = str(options.get("mode") or "")
    body = _with_log_header(body, n)
    if not _log_header_ok(body, n):
        return refuse("card.body_invalid", "checkpoint log last row is not cp n-1")
    when = datetime.now(_PT).strftime("%Y-%m-%d %H:%M %Z")
    trigger = str(options.get("trigger") or "")
    if mode == "light":
        cell = "none"
    else:
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
        session_id = str((seal or {}).get("session_id") or "")
        attribution = str(options.get("bus_identity") or "")
        suppressed = str((seal or {}).get("event_suppressed_reason") or "")
        seal_cite = ""
        if session_id:
            from cortex_store.verbatim_succession import transcript_messages_path

            seal_path = transcript_messages_path(
                f"notes/system/transcripts/{session_id}.md"
            )
            seal_cite = f"{seal_path} · "
        suppressed_tok = f" · event_suppressed:{suppressed}" if suppressed else ""
        cell = (
            f"{seal_cite}{msg} · provenance:{prov} · "
            f"producer:{prod} · capture:{cap_tok} · attribution:{attribution}"
            f"{suppressed_tok}"
        )
    row = (
        f"| {n} | {when} | {trigger} | {expected} | {cell} | pipeline {execution_id} |"
    )
    updated = body.rstrip() + "\n" + row + "\n"
    data = updated.encode("utf-8")
    written = await fs.write(uri, data, expected_sha256=live)
    if not written.get("ok"):
        return refuse("card.conflict", "409 card write")
    result = {
        "refused": None,
        "replayed": False,
        "uri": uri,
        "prior_sha256": expected,
        "written_sha256": written["sha256"],
        "row": row,
        "warnings": warnings,
        "notices": notices,
    }
    if mode != "light":
        result["snapshot_uri"] = str(envelope_step.get("snapshot_uri") or "")
        result["snapshot_sha256"] = str(envelope_step.get("sha256") or "")
    return result


class GrokbotCheckpointCardHandler(BaseHandler):
    step_type = "grokbot_checkpoint_card_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        outputs = getattr(context, "outputs", {}) or {}
        source = step_output_json(outputs, "source")
        if (not source or source.get("_skipped")) and options.get("mode") == "harvest_seal":
            source = step_output_json(outputs, "harvest_pour")
        payload = await run_card(
            options,
            source,
            step_output_json(outputs, "envelope"),
            fs_of(context),
            execution_id=str(getattr(context, "execution_id", "") or ""),
            seal=step_output_json(outputs, "seal"),
        )
        return StepOutput(raw="", json=payload)
