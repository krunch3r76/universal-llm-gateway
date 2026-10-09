"""Assemble a read-only pin ledger and the writer prompt blocks.

The pipeline executor calls ``WritingAssembleHandler`` as ``writing_assemble_v1``.
Invalid options and briefs return a refusal payload so later generate steps
stay skipped. The handler never calls a model and never writes a file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder

from ._working_set import WorkingSetClient, WorkingSetUnavailable

_EXCERPT_MAX = 1200
_EXCERPT_SUFFIX = " [truncated]"
_FENCE_TAG = re.compile(
    r"</?(?:violations|findings|documents|draft|brief|pin)\b",
    re.IGNORECASE,
)
_OUTPUTS = frozenset({"envelope", "packet"})


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def _unwrap(value: Any, default: Any) -> Any:
    if isinstance(value, dict) and "type" in value and "default" in value:
        return value.get("default", default)
    if value is None:
        return default
    return value


def _fence(text: str) -> str:
    return _FENCE_TAG.sub(lambda match: "&lt;" + match.group(0)[1:], text)


def _xml_attr(value: Any) -> str:
    return str(value).replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str) -> str:
    if len(text) <= _EXCERPT_MAX:
        return text
    keep = _EXCERPT_MAX - len(_EXCERPT_SUFFIX)
    return text[:keep] + _EXCERPT_SUFFIX


def _record_text(record: Any) -> str:
    if isinstance(record, dict) and isinstance(record.get("result"), dict):
        record = record["result"]
    if isinstance(record, dict):
        for key in (
            "text",
            "body",
            "content",
            "statement",
            "excerpt",
            "description",
            "notes",
            "summary",
            "title",
            "name",
        ):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(record, default=str)
    return str(record)


def _bus_text(payload: Any) -> str:
    if isinstance(payload, list):
        turns = payload
    elif isinstance(payload, dict) and isinstance(payload.get("turns"), list):
        turns = payload["turns"]
    elif isinstance(payload, dict) and ("body" in payload or "text" in payload):
        return str(payload.get("body") or payload.get("text") or "")
    elif isinstance(payload, dict):
        turns = [payload]
    else:
        return str(payload)
    parts: list[str] = []
    for turn in turns:
        if isinstance(turn, dict):
            parts.append(str(turn.get("body") or turn.get("text") or ""))
        else:
            parts.append(str(turn))
    return "\n".join(part for part in parts if part)


def _parse_ref(ref: str) -> tuple[str, Any] | None:
    if ref.startswith("entity:") and ref[len("entity:") :]:
        return ("entity", ref[len("entity:") :])
    if ref.startswith("a:") and ref[2:].isdigit():
        return ("assertion", int(ref[2:]))
    if ref.startswith("cortex://") and len(ref) > len("cortex://"):
        return ("note", ref)
    if ref.startswith("agent-bus:"):
        rest = ref[len("agent-bus:") :]
        if "#" in rest:
            thread, turn = rest.split("#", 1)
            if thread and turn.isdigit():
                return ("bus", (thread, int(turn)))
        elif rest:
            return ("bus", (rest, None))
    return None


def _writer_template() -> str:
    path = Path(__file__).resolve().parents[1] / "prompts.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return str((data.get("prompts") or {}).get("writer", {}).get("template") or "")


def _brief_lines(brief: dict[str, Any]) -> str:
    material = ", ".join(_fence(item) for item in brief["material_attested"])
    missing = ", ".join(_fence(item) for item in brief["not_attested"])
    standing = _fence(str(brief.get("standing_limits") or ""))
    voice_raw = brief.get("voice_anchor")
    voice = _fence(str(voice_raw)) if voice_raw else "none"
    priority = _fence(str(brief.get("objective_priority") or ""))
    deliverable = brief["deliverable"]
    parts = [_fence(str(deliverable["genre"]))]
    if deliverable.get("length_band"):
        parts.append(_fence(str(deliverable["length_band"])))
    if deliverable.get("format"):
        parts.append(_fence(str(deliverable["format"])))
    lines = [
        f"Signer: {_fence(str(brief['signer']))}",
        f"Recipient: {_fence(str(brief['recipient']))}",
        f"Objective: {_fence(str(brief['objective']))}",
        f"Material attested: {material}",
        f"Not attested (do not state as fact): {missing}",
        f"Standing limits: {standing}",
        f"Voice anchor: {voice}",
        f"Objective priority: {priority}",
        f"Deliverable: {', '.join(parts)}",
    ]
    return "\n".join(lines)


class WritingAssembleHandler(BaseHandler):
    """Resolve a working set into pins, prompt blocks, and an optional packet.

    The executor calls ``execute`` once per run. A refusal sets ``refused``
    and leaves ``error`` on the step output empty so the GO condition can
    skip generate steps. Inject ``client`` in tests; production builds a
    ``WorkingSetClient`` only after options and refs validate.
    """

    step_type = "writing_assemble_v1"

    def __init__(self, client: WorkingSetClient | None = None) -> None:
        super().__init__()
        self._client = client

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Validate options and the brief, then build the ledger and blocks.

        ``context.options`` supplies ``working_set``, ``brief``, seats, and
        ``output``. Returns a ``StepOutput`` whose JSON is either the success
        payload or a refusal. Does not call a model or mutate the working set.
        """
        del step
        options = getattr(context, "options", {}) or {}
        refusal = _validate_options(options)
        if refusal is not None:
            return _step(refusal)
        brief_or_error = _validate_brief(options.get("brief"))
        if isinstance(brief_or_error, dict) and brief_or_error.get("refused"):
            return _step(brief_or_error)
        brief = brief_or_error
        assert isinstance(brief, dict)
        seats = _resolve_seats(options, brief)
        if seats.get("refused"):
            return _step(seats)
        parsed, bad = _parse_working_set(options.get("working_set"))
        if bad is not None:
            return _step(bad)
        client = self._client or WorkingSetClient()
        try:
            pins = await _resolve_pins(client, parsed)
        except WorkingSetUnavailable as exc:
            return _step(
                {
                    "ok": False,
                    "refused": "working_set_unavailable",
                    "error": exc.reason,
                    "ref": exc.ref,
                }
            )
        pins.extend(_not_attested_pins(brief["not_attested"], start=len(pins) + 1))
        max_pins = int(_unwrap(options.get("max_pins", 40), 40))
        if len(pins) > max_pins:
            return _step(
                {
                    "ok": False,
                    "refused": "brief_invalid",
                    "error": "max_pins_exceeded",
                }
            )
        documents = "\n".join(_pin_line(pin) for pin in pins)
        brief_block = _brief_lines(brief)
        packet = PromptBuilder().render(
            _writer_template(),
            {"documents": documents, "brief": brief_block},
        )
        output = str(_unwrap(options.get("output", "envelope"), "envelope"))
        return _step(
            {
                "ok": True,
                "refused": None,
                "pin_ledger": pins,
                "documents_block": documents,
                "brief_block": brief_block,
                "packet": packet,
                "writer_seat": seats["writer_seat"],
                "reviewer_seat": seats["reviewer_seat"],
                "sensitivity": seats["sensitivity"],
                "dispatch_thread_id": seats["dispatch_thread_id"],
                "output": output,
            }
        )


def _validate_options(options: dict[str, Any]) -> dict[str, Any] | None:
    output = str(_unwrap(options.get("output", "envelope"), "envelope"))
    if output not in _OUTPUTS:
        return {
            "ok": False,
            "refused": "options_invalid",
            "error": "output must be envelope or packet",
        }
    raw_max = _unwrap(options.get("max_pins", 40), 40)
    if isinstance(raw_max, bool) or not isinstance(raw_max, int):
        return {
            "ok": False,
            "refused": "options_invalid",
            "error": "max_pins must be an integer",
        }
    return None


_WRITER_SEATS = frozenset({"local", "cdp", "cursor_pool1"})
_REVIEWER_SEATS = frozenset({"auto", "local", "cdp"})
_DEFAULT_THREAD = "15790"
_REFUSED_THREAD = "12286"


def _resolve_seats(options: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    raw_sensitivity = brief.get("sensitivity")
    if raw_sensitivity is None or str(raw_sensitivity).strip() == "":
        sensitivity = "sensitive"
    else:
        sensitivity = str(raw_sensitivity).strip()
        if sensitivity not in {"sensitive", "non_sensitive"}:
            return {
                "ok": False,
                "refused": "brief_invalid",
                "error": "sensitivity must be sensitive or non_sensitive",
            }
    writer_seat = str(_unwrap(options.get("writer_seat", "local"), "local"))
    if writer_seat not in _WRITER_SEATS:
        return {
            "ok": False,
            "refused": "writer_seat_unavailable",
            "error": "writer_seat must be local, cdp, or cursor_pool1",
        }
    reviewer_given = str(_unwrap(options.get("reviewer_seat", "auto"), "auto"))
    if reviewer_given not in _REVIEWER_SEATS:
        return {
            "ok": False,
            "refused": "reviewer_seat_unavailable",
            "error": "reviewer_seat must be auto, local, or cdp",
        }
    reviewer_seat = reviewer_given
    if reviewer_seat == "auto":
        reviewer_seat = "cdp" if sensitivity == "sensitive" else "local"
    if reviewer_seat == "local" and sensitivity == "sensitive":
        return {
            "ok": False,
            "refused": "reviewer_seat_unavailable",
            "error": "local reviewer is for non-sensitive briefs only (D2)",
        }
    allow = options.get("allow_partial_independence", False)
    if isinstance(allow, dict) and "default" in allow:
        allow = allow.get("default", False)
    if writer_seat == "cdp" and reviewer_seat == "cdp" and allow is not True:
        return {
            "ok": False,
            "refused": "reviewer_seat_unavailable",
            "error": (
                "no independent reviewer seat for writer_seat=cdp on a "
                "sensitive brief; use writer_seat=local or mark the brief non_sensitive"
            ),
        }
    thread = str(
        _unwrap(options.get("dispatch_thread_id", _DEFAULT_THREAD), _DEFAULT_THREAD)
    ).strip()
    if writer_seat != "local" or reviewer_seat != "local":
        if not thread:
            return {
                "ok": False,
                "refused": "options_invalid",
                "error": "dispatch_thread_id_required",
            }
        if thread == _REFUSED_THREAD:
            return {
                "ok": False,
                "refused": "options_invalid",
                "error": "dispatch_thread_id_refused",
            }
        if not thread.isdigit():
            return {
                "ok": False,
                "refused": "options_invalid",
                "error": "dispatch_thread_id_invalid",
            }
    return {
        "writer_seat": writer_seat,
        "reviewer_seat": reviewer_seat,
        "sensitivity": sensitivity,
        "dispatch_thread_id": thread or _DEFAULT_THREAD,
    }


def _validate_brief(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "refused": "brief_invalid",
            "error": "missing: signer, objective, recipient, deliverable.genre",
        }
    missing: list[str] = []
    for field in ("signer", "objective", "recipient"):
        if not str(raw.get(field) or "").strip():
            missing.append(field)
    deliverable = raw.get("deliverable")
    if (
        not isinstance(deliverable, dict)
        or not str(deliverable.get("genre") or "").strip()
    ):
        missing.append("deliverable.genre")
    for field in ("material_attested", "not_attested"):
        values = raw.get(field, [])
        if not isinstance(values, list) or not all(
            isinstance(item, str) for item in values
        ):
            missing.append(field)
    if missing:
        return {
            "ok": False,
            "refused": "brief_invalid",
            "error": "missing: " + ", ".join(missing),
        }
    brief = dict(raw)
    brief["material_attested"] = list(raw.get("material_attested") or [])
    brief["not_attested"] = list(raw.get("not_attested") or [])
    brief["deliverable"] = deliverable
    return brief


def _parse_working_set(
    raw: Any,
) -> tuple[list[tuple[str, str, Any]], dict[str, Any] | None]:
    refs = raw or []
    if not isinstance(refs, list):
        return [], {
            "ok": False,
            "refused": "brief_invalid",
            "error": "missing: working_set",
        }
    parsed: list[tuple[str, str, Any]] = []
    for ref in refs:
        if not isinstance(ref, str):
            return [], {
                "ok": False,
                "refused": "brief_invalid",
                "error": f"bad_ref: {ref}",
            }
        spec = _parse_ref(ref)
        if spec is None:
            return [], {
                "ok": False,
                "refused": "brief_invalid",
                "error": f"bad_ref: {ref}",
            }
        kind, value = spec
        parsed.append((ref, kind, value))
    return parsed, None


async def _resolve_pins(
    client: WorkingSetClient,
    parsed: list[tuple[str, str, Any]],
) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    for index, (ref, kind, value) in enumerate(parsed, start=1):
        if kind == "entity":
            record = await client.entity_get(value)
            excerpt, digest = _record_text(record), None
        elif kind == "assertion":
            record = await client.assertion_get(value)
            excerpt, digest = _record_text(record), None
        elif kind == "note":
            text, digest = await client.read_note(value)
            excerpt = text
        else:
            thread, turn = value
            record = await client.bus_read(thread, turn)
            excerpt, digest = _bus_text(record), None
        pins.append(
            {
                "pin_id": f"P{index}",
                "source_uri": ref,
                "kind": kind,
                "excerpt": _truncate(_fence(excerpt)),
                "sha256": digest,
                "attested": True,
            }
        )
    return pins


def _not_attested_pins(items: list[str], *, start: int) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    for offset, item in enumerate(items):
        pins.append(
            {
                "pin_id": f"P{start + offset}",
                "source_uri": f"brief:not_attested[{offset}]",
                "kind": "not_attested",
                "excerpt": _truncate(_fence(item)),
                "sha256": None,
                "attested": False,
            }
        )
    return pins


def _pin_line(pin: dict[str, Any]) -> str:
    attested = "true" if pin["attested"] else "false"
    source = _xml_attr(pin["source_uri"])
    return (
        f'<pin id="{pin["pin_id"]}" source="{source}" '
        f'attested="{attested}">{pin["excerpt"]}</pin>'
    )
