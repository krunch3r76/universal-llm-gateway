"""Admit a writer or reviewer seat through team_dispatch.

``WritingSeatDispatchHandler`` is ``writing_seat_dispatch_v1``. A local seat
returns skipped. Thread refusals return before any HTTP client is built.
The payload carries ``prompt_sha256`` and never the prompt text, and it
never sets ``StepOutput.error``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder
from transport_utils import DEFAULT_STARGATE_URL, make_async_client

from .assemble import _unwrap

DEFAULT_DISPATCH_THREAD = "15790"
REFUSED_THREAD = "12286"
_CALLER = "pipeline:writer-specialist-v1"
_JSON_ONLY = "Return only the JSON object, with no prose before or after it."
_POSTED: set[str] = set()
_DEFAULT_MODELS = {"cdp": "cdp/opus-5.5", "cursor_pool1": "cursor/grok-4.7"}


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload, error=None)


def _role(step: Any) -> str:
    name = str(getattr(step, "name", "") or "")
    if name.startswith("review"):
        return "reviewer"
    if name.startswith("draft"):
        return "writer"
    if hasattr(step, "get_domain_field"):
        extra = step.get_domain_field("role")
        if extra in {"writer", "reviewer"}:
            return str(extra)
    return "writer"


def _prompts() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "prompts.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("prompts") or {}


def _thread_id(options: dict[str, Any]) -> str:
    raw = options.get("dispatch_thread_id", DEFAULT_DISPATCH_THREAD)
    return str(_unwrap(raw, DEFAULT_DISPATCH_THREAD)).strip()


def _seat_models(options: dict[str, Any]) -> dict[str, Any]:
    raw = _unwrap(options.get("seat_models", _DEFAULT_MODELS), _DEFAULT_MODELS)
    return raw if isinstance(raw, dict) else dict(_DEFAULT_MODELS)


def _timeout(options: dict[str, Any]) -> int:
    raw = _unwrap(options.get("seat_timeout_s", 600), 600)
    return int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else 600


def _run_id(context: Any) -> str:
    existing = getattr(context, "execution_id", None)
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    minted = getattr(context, "_writing_run_id", None)
    if not isinstance(minted, str) or not minted:
        import uuid

        minted = str(uuid.uuid4())
        try:
            context._writing_run_id = minted
        except Exception:
            return minted
    return minted


async def _post_json(client: Any, path: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = await client.post(path, json=body)
    except Exception as exc:
        return {"error": f"transport_error: {exc}", "transport": True}
    try:
        data = resp.json()
    except Exception:
        data = {"raw": (getattr(resp, "text", "") or "")[:300]}
    if not isinstance(data, dict):
        data = {"value": data}
    status = getattr(resp, "status_code", 200)
    if isinstance(status, int) and status >= 400:
        data.setdefault("error", f"http_{status}")
        data["http_status"] = status
    return data


class WritingSeatDispatchHandler(BaseHandler):
    """POST one team_dispatch admit for a non-local writing seat.

    Role comes from ``step.name`` (``draft_*`` writer, ``review_*`` reviewer).
    A repeated idempotency key in this process does not POST again.
    """

    step_type = "writing_seat_dispatch_v1"

    def __init__(self, client: Any | None = None) -> None:
        super().__init__()
        self._client = client

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Admit the seat or return a typed refusal without ``StepOutput.error``.

        Local seats skip. Thread ``12286``, an empty id, and a non-digit id
        refuse before ``make_async_client``. Transport errors are not retried.
        """
        role = _role(step)
        options = getattr(context, "options", {}) or {}
        outputs = getattr(context, "outputs", {}) or {}
        assemble = getattr(outputs.get("assemble"), "json", None)
        if not isinstance(assemble, dict):
            assemble = {}
        seat = assemble.get("writer_seat" if role == "writer" else "reviewer_seat")
        if seat == "local":
            return _step({"ok": True, "skipped": True})
        thread = _thread_id(options)
        if not thread:
            return _step(
                {"ok": False, "refused": "dispatch_thread_id_required", "posted": False}
            )
        if thread == REFUSED_THREAD:
            return _step(
                {"ok": False, "refused": "dispatch_thread_id_refused", "posted": False}
            )
        if not thread.isdigit():
            return _step(
                {"ok": False, "refused": "dispatch_thread_id_invalid", "posted": False}
            )
        run_id = _run_id(context)
        key = f"writer-specialist-v1:{run_id}:{role}"
        if key in _POSTED:
            return _step(
                {"ok": False, "duplicate_suppressed": True, "idempotency_key": key}
            )
        prompts = _prompts()
        row = prompts.get("writer" if role == "writer" else "reviewer") or {}
        fields: dict[str, Any] = {
            "documents": assemble.get("documents_block") or "",
            "brief": assemble.get("brief_block") or "",
        }
        if role == "reviewer":
            draft = getattr(outputs.get("draft"), "json", None)
            text = draft.get("draft") if isinstance(draft, dict) else ""
            fields["draft"] = text if isinstance(text, str) else ""
        prompt = PromptBuilder().render(str(row.get("template") or ""), fields)
        prompt = prompt.rstrip() + "\n" + _JSON_ONLY
        system = str(row.get("system_prompt") or "")
        models = _seat_models(options)
        timeout_s = _timeout(options)
        model = str(models.get(str(seat)) or "")
        body: dict[str, Any] = {
            "op": "generate",
            "model": model,
            "prompt": prompt,
            "system": system,
            "dispatch_thread_id": thread,
            "caller_agent": _CALLER,
            "transcript_id": key,
        }
        if seat == "cdp":
            body["job"] = "confer"
            body["mcp"] = False
            body["timeout_seconds"] = timeout_s
        else:
            body["seat"] = "cursor-sdk"
            body["lane"] = "B"
            body["job"] = "freeform"
            body["read_only"] = True
            body["work_key"] = f"packet:writer-specialist-v1:{run_id}:{role}"
            body["model_knobs"] = {"fast": "true"}
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        _POSTED.add(key)
        if self._client is not None:
            dispatched = await _post_json(self._client, "/api/v1/team/dispatch", body)
        else:
            async with make_async_client(DEFAULT_STARGATE_URL, timeout=30.0) as client:
                dispatched = await _post_json(client, "/api/v1/team/dispatch", body)
        base = {
            "seat": seat,
            "role": role,
            "model": model,
            "dispatch_thread_id": thread,
            "idempotency_key": key,
            "prompt_sha256": digest,
        }
        if dispatched.get("transport"):
            return _step(
                {
                    "ok": False,
                    "admit": "unknown",
                    "error": str(dispatched.get("error") or "")[:500],
                    "idempotency_key": key,
                    "prompt_sha256": digest,
                }
            )
        status = dispatched.get("http_status")
        if isinstance(status, int) and status >= 400:
            return _step(
                {
                    "ok": False,
                    "admit": "failed",
                    "http_status": status,
                    "error": str(dispatched.get("error") or "")[:500],
                    "idempotency_key": key,
                    "prompt_sha256": digest,
                }
            )
        return _step(
            {
                "ok": True,
                "admit": "admitted",
                **base,
                "execution_id": dispatched.get("execution_id"),
                "poll_hint": dispatched.get("poll_hint"),
                "dispatch": dispatched,
            }
        )
