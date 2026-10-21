"""Assemble a read-only pin ledger and the writer prompt blocks.

The pipeline executor calls ``WritingAssembleHandler`` as ``writing_assemble_v1``.
Invalid options and briefs return a refusal payload so later generate steps
stay skipped. The handler never calls a model and never writes a file.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder
from writing_intake.fact_inventory import build_fact_inventory
from writing_intake.schema import validate_intake_payload

from ._working_set import WorkingSetClient, WorkingSetUnavailable

_QUOTA_MESSAGE_TOKEN_RE = re.compile(
    r"\b(insufficient_quota|billing_hard_limit_reached)\b"
)
_EXCERPT_MAX = 1200
_EXCERPT_SUFFIX = " [truncated]"
_FENCE_TAG = re.compile(
    r"</?(?:violations|findings|documents|draft|brief|pin|verbatim_notes|recipient|"
    r"objective|channel|max_chars|not_attested|architect_brief)\b",
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
        messages = getattr(context, "messages", None)
        if messages is None:
            messages = options.get("messages")
        refusal = _validate_options(options)
        if refusal is not None:
            return _step(refusal)
        intake, intake_refusal = validate_intake_payload(options, messages)
        if intake_refusal is not None:
            return _step(intake_refusal)
        if intake is not None:
            return await self._execute_intake(intake, options)
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
            {
                "documents": documents,
                "brief": brief_block,
                "verbatim_notes": "",
                "architect_brief": "",
            },
        )
        output = str(_unwrap(options.get("output", "envelope"), "envelope"))
        success: dict[str, Any] = {
            "ok": True,
            "refused": None,
            "pin_ledger": pins,
            "documents_block": documents,
            "brief_block": brief_block,
            "verbatim_notes_block": "",
            "packet": packet,
            "writer_seat": seats["writer_seat"],
            "reviewer_seat": seats["reviewer_seat"],
            "sensitivity": seats["sensitivity"],
            "dispatch_thread_id": seats["dispatch_thread_id"],
            "output": output,
        }
        if seats.get("writer_model"):
            success["writer_model"] = seats["writer_model"]
        if seats.get("reviewer_model"):
            success["reviewer_model"] = seats["reviewer_model"]
        return _step(success)

    async def _execute_intake(self, intake: Any, options: dict[str, Any]) -> StepOutput:
        data = intake.model_dump(exclude_none=True)
        if data.get("mode") == "fact_inventory":
            return await self._execute_fact_inventory(data, options)
        parsed, bad = _parse_working_set(data.get("working_set") or options.get("working_set"))
        if bad is not None:
            return _step(bad)
        client = self._client or WorkingSetClient()
        try:
            ws_pins = await _resolve_pins(client, parsed)
        except WorkingSetUnavailable as exc:
            return _step(
                {
                    "ok": False,
                    "refused": "working_set_unavailable",
                    "error": exc.reason,
                    "ref": exc.ref,
                }
            )
        pins = _verbatim_note_pins(data["verbatim_notes"], start=1)
        offset = len(pins) + 1
        for index, pin in enumerate(ws_pins, start=offset):
            pin["pin_id"] = f"P{index}"
            pins.append(pin)
            offset = index + 1
        for offset, item in enumerate(list(data.get("not_attested") or [])):
            pins.append(
                {
                    "pin_id": f"P{len(pins) + 1}",
                    "source_uri": f"intake:not_attested[{offset}]",
                    "kind": "not_attested",
                    "excerpt": _truncate(_fence(item)),
                    "sha256": None,
                    "attested": False,
                }
            )
        max_pins = int(_unwrap(options.get("max_pins", 40), 40))
        if len(pins) > max_pins:
            return _step(
                {
                    "ok": False,
                    "refused": "brief_invalid",
                    "error": "max_pins_exceeded",
                }
            )
        blocks = _intake_blocks(data, pins)
        output = str(_unwrap(options.get("output", "envelope"), "envelope"))
        packet = PromptBuilder().render(
            _writer_template(),
            {
                "documents": blocks["documents_block"],
                "brief": blocks["brief_block"],
                "architect_brief": "",
                "verbatim_notes": blocks["verbatim_notes_block"],
            },
        )
        return _step(
            {
                "ok": True,
                "refused": None,
                "pin_ledger": pins,
                "verbatim_notes_block": blocks["verbatim_notes_block"],
                "documents_block": blocks["documents_block"],
                "brief_block": blocks["brief_block"],
                "intake": data,
                "max_chars": data.get("max_chars"),
                "channel": data.get("channel"),
                "packet": packet,
                "output": output,
            }
        )

    async def _execute_fact_inventory(
        self, data: dict[str, Any], options: dict[str, Any]
    ) -> StepOutput:
        inventory = build_fact_inventory(
            str(data.get("prior_draft") or ""),
            purpose=str(data.get("purpose") or data.get("objective") or ""),
            audience=str(data.get("audience") or data.get("recipient") or ""),
            attested_sources=list(data.get("attested_sources") or []),
            verbatim_notes=list(data.get("verbatim_notes") or []) or None,
        )
        data = {
            **data,
            "verbatim_notes": inventory["verbatim_notes"],
            "recipient": data.get("recipient") or data.get("audience"),
            "objective": data.get("objective") or data.get("purpose"),
        }
        parsed, bad = _parse_working_set(data.get("working_set") or options.get("working_set"))
        if bad is not None:
            return _step(bad)
        client = self._client or WorkingSetClient()
        try:
            ws_pins = await _resolve_pins(client, parsed)
        except WorkingSetUnavailable as exc:
            return _step(
                {
                    "ok": False,
                    "refused": "working_set_unavailable",
                    "error": exc.reason,
                    "ref": exc.ref,
                }
            )
        pins = _verbatim_note_pins(inventory["load_bearing_claims"], start=1)
        offset = len(pins) + 1
        for index, pin in enumerate(ws_pins, start=offset):
            pin["pin_id"] = f"P{index}"
            pins.append(pin)
        max_pins = int(_unwrap(options.get("max_pins", 40), 40))
        if len(pins) > max_pins:
            return _step(
                {
                    "ok": False,
                    "refused": "brief_invalid",
                    "error": "max_pins_exceeded",
                }
            )
        blocks = _intake_blocks(data, pins)
        if str(data.get("channel") or "") == "filing":
            blocks["brief_block"] = (
                blocks["brief_block"]
                + "\nFiling rule: every exhibit-backed proposition must carry (Exhibit N); "
                "no one-fact-per-paragraph listicles without exhibit tags."
            )
        output = str(_unwrap(options.get("output", "envelope"), "envelope"))
        packet = PromptBuilder().render(
            _writer_template(),
            {
                "documents": blocks["documents_block"],
                "brief": blocks["brief_block"],
                "architect_brief": "",
                "verbatim_notes": blocks["verbatim_notes_block"],
            },
        )
        return _step(
            {
                "ok": True,
                "refused": None,
                "pin_ledger": pins,
                "verbatim_notes_block": blocks["verbatim_notes_block"],
                "documents_block": blocks["documents_block"],
                "brief_block": blocks["brief_block"],
                "intake": data,
                "max_chars": data.get("max_chars"),
                "channel": data.get("channel"),
                "mode": "fact_inventory",
                "dropped_facts": inventory["dropped_facts"],
                "load_bearing_claims": inventory["load_bearing_claims"],
                "packet": packet,
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
_REVIEWER_SEATS = frozenset({"auto", "local", "cdp", "openai"})
_DEFAULT_THREAD = "15790"
_REFUSED_THREAD = "12286"
_DEFAULT_SEAT_MODELS = {
    "cdp": "cdp/opus-5.5",
    "cursor_pool1": "cursor/grok-4.7",
    "openai": "openai/gpt-5.5",
}


def _seat_models(options: dict[str, Any]) -> dict[str, str]:
    raw = _unwrap(options.get("seat_models", _DEFAULT_SEAT_MODELS), _DEFAULT_SEAT_MODELS)
    if not isinstance(raw, dict):
        return dict(_DEFAULT_SEAT_MODELS)
    merged = dict(_DEFAULT_SEAT_MODELS)
    for key, value in raw.items():
        if value is not None:
            merged[str(key)] = str(value)
    return merged


def _writer_model_id(options: dict[str, Any], writer_seat: str) -> str:
    override = options.get("writer_model")
    if isinstance(override, str) and override.strip():
        return override.strip()
    if writer_seat == "local":
        from .independence import load_writing_models

        return load_writing_models().get("writer") or ""
    return _seat_models(options).get(writer_seat, "")


def _is_anthropic_api_writer(model_id: str) -> bool:
    return str(model_id or "").lower().startswith("anthropic/")


_QUOTA_CODES = frozenset({"insufficient_quota", "billing_hard_limit_reached"})


def resolve_openai_reviewer_model(seat_models: dict[str, str] | None = None) -> str:
    """Return a carded openai/gpt-5.x model id for an openai reviewer seat."""
    from model_capabilities.model_capabilities import MODEL_CAPABILITY_CARDS

    models = seat_models or _DEFAULT_SEAT_MODELS
    raw = str(models.get("openai") or _DEFAULT_SEAT_MODELS["openai"])
    if raw in MODEL_CAPABILITY_CARDS and raw.lower().startswith("openai/gpt-5"):
        return raw
    raise ValueError(
        f"openai reviewer model {raw!r} is not a carded openai/gpt-5.x id"
    )


def _quota_code_in_mapping(data: dict[str, Any]) -> str | None:
    for key in ("error_code", "code", "type"):
        token = str(data.get(key) or "")
        if token in _QUOTA_CODES:
            return token
        lowered = token.lower()
        if lowered in _QUOTA_CODES:
            return lowered
    error = data.get("error")
    if isinstance(error, dict):
        for key in ("code", "type"):
            token = str(error.get(key) or "")
            if token in _QUOTA_CODES:
                return token
            lowered = token.lower()
            if lowered in _QUOTA_CODES:
                return lowered
    upstream = data.get("upstream_error")
    if isinstance(upstream, dict):
        nested = upstream.get("error")
        if isinstance(nested, dict):
            for key in ("code", "type"):
                token = str(nested.get(key) or "")
                if token in _QUOTA_CODES:
                    return token
                lowered = token.lower()
                if lowered in _QUOTA_CODES:
                    return lowered
    return None


def _walk_quota_payload(record: dict[str, Any]) -> str | None:
    stack: list[Any] = [record]
    seen: set[int] = set()
    while stack:
        obj = stack.pop()
        if not isinstance(obj, dict):
            continue
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        code = _quota_code_in_mapping(obj)
        if code:
            return code
        for key in ("data", "detail", "error", "upstream_error", "upstream"):
            nested = obj.get(key)
            if isinstance(nested, dict):
                stack.append(nested)
    return None


def _quota_token_in_message(message: str) -> str | None:
    match = _QUOTA_MESSAGE_TOKEN_RE.search(message)
    return match.group(1) if match else None


def openai_reviewer_quota_reason(record: dict[str, Any]) -> str | None:
    """Return the matched OpenAI quota token, or None when not a quota failure."""
    structured = _walk_quota_payload(record)
    if structured:
        return structured
    error = record.get("error")
    if isinstance(error, dict):
        token = _quota_token_in_message(str(error.get("message") or ""))
        if token:
            return token
        structured = _walk_quota_payload(error)
        if structured:
            return structured
    message = record.get("message")
    if isinstance(message, str):
        token = _quota_token_in_message(message)
        if token:
            return token
    return None


def is_openai_reviewer_quota_error(record: dict[str, Any]) -> bool:
    """True when an OpenAI reviewer dispatch or execution failed for quota."""
    return openai_reviewer_quota_reason(record) is not None


def build_held_reviewer_unfunded_review(*, reason: str = "insufficient_quota") -> dict[str, Any]:
    """Review-step payload when OpenAI reviewer credit is unavailable."""
    return {
        "ok": False,
        "refused": "held_reviewer_unfunded",
        "status": "held_reviewer_unfunded",
        "reason": reason,
        "findings": None,
        "verdict": None,
    }


async def post_reviewer_unfunded_bus_report(
    *,
    thread_id: str,
    reason: str,
    writer_model: str,
    reviewer_model: str,
    bus_client: Any | None = None,
) -> bool:
    """Emit a held_reviewer_unfunded bus turn; False when transport/post fails."""
    import os

    from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

    body = (
        "writer-specialist-v1: review held — OpenAI reviewer unfunded "
        f"({reason}); writer={writer_model!r} reviewer={reviewer_model!r}; "
        "no anthropic or cdp reviewer fallback."
    )
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {
        "thread": thread_id,
        "from": "pipeline:writer-specialist-v1",
        "to": "web-anthropic",
        "subject": "writer-specialist-v1 review held",
        "body": body,
        "status": "open",
    }
    try:
        if bus_client is not None:
            resp = await bus_client.post("/turns", json=payload, headers=headers)
            status = getattr(resp, "status_code", 200)
            return isinstance(status, int) and status < 400
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = await client.post("/turns", json=payload, headers=headers)
            return resp.status_code < 400
    except Exception:
        return False


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
            "error": "reviewer_seat must be auto, local, cdp, or openai",
        }
    writer_model = _writer_model_id(options, writer_seat)
    anthropic_writer = _is_anthropic_api_writer(writer_model)
    reviewer_seat = reviewer_given
    if reviewer_seat == "auto":
        if anthropic_writer:
            reviewer_seat = "openai"
        else:
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
    if (
        anthropic_writer
        and reviewer_given == "cdp"
        and allow is not True
    ):
        return {
            "ok": False,
            "refused": "reviewer_seat_unavailable",
            "error": (
                "anthropic API writer requires an independent openai reviewer; "
                "cdp reviewer refused"
            ),
        }
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
    payload: dict[str, Any] = {
        "writer_seat": writer_seat,
        "reviewer_seat": reviewer_seat,
        "sensitivity": sensitivity,
        "dispatch_thread_id": thread or _DEFAULT_THREAD,
    }
    if anthropic_writer:
        payload["writer_model"] = writer_model
    if reviewer_seat == "openai":
        try:
            payload["reviewer_model"] = resolve_openai_reviewer_model(
                _seat_models(options)
            )
        except ValueError as exc:
            return {
                "ok": False,
                "refused": "reviewer_seat_unavailable",
                "error": str(exc),
            }
    return payload


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


def _verbatim_note_pins(items: list[str], *, start: int) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    for offset, item in enumerate(items):
        pins.append(
            {
                "pin_id": f"P{start + offset}",
                "source_uri": f"intake:verbatim_notes[{offset}]",
                "kind": "verbatim_note",
                "excerpt": _truncate(_fence(item)),
                "sha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
                "attested": True,
            }
        )
    return pins


def _intake_blocks(intake: dict[str, Any], pins: list[dict[str, Any]]) -> dict[str, str]:
    notes = "\n".join(
        f"<note id=\"{pin['pin_id']}\">{pin['excerpt']}</note>"
        for pin in pins
        if pin.get("kind") == "verbatim_note"
    )
    verbatim_notes_block = f"<verbatim_notes>\n{notes}\n</verbatim_notes>"
    not_attested = ", ".join(_fence(item) for item in intake.get("not_attested") or [])
    max_chars = intake.get("max_chars")
    brief_lines = [
        f"Recipient: {_fence(str(intake.get('recipient') or ''))}",
        f"Objective: {_fence(str(intake.get('objective') or ''))}",
        f"Channel: {_fence(str(intake.get('channel') or ''))}",
        f"Max chars: {max_chars if max_chars is not None else 'none'}",
        f"Not attested: {not_attested or 'none'}",
    ]
    brief_block = "<brief>\n" + "\n".join(brief_lines) + "\n</brief>"
    documents_block = "\n".join(_pin_line(pin) for pin in pins)
    return {
        "verbatim_notes_block": verbatim_notes_block,
        "brief_block": brief_block,
        "documents_block": documents_block,
    }


def _pin_line(pin: dict[str, Any]) -> str:
    attested = "true" if pin["attested"] else "false"
    source = _xml_attr(pin["source_uri"])
    return (
        f'<pin id="{pin["pin_id"]}" source="{source}" '
        f'attested="{attested}">{pin["excerpt"]}</pin>'
    )
