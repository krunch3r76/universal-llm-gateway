"""Assertion-enrichment handlers — idempotent prospective + events writeback."""

from __future__ import annotations

import json
import logging
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_CORTEX_URL, make_async_client

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 15.0


def _step_output(payload: dict[str, Any], *, error: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload, default=str), json=payload, error=error)


def _kinds_enabled(context: Any) -> set[str]:
    opts = getattr(context, "options", {}) or {}
    raw = opts.get("kinds")
    if isinstance(raw, list):
        return {str(k).strip() for k in raw if str(k).strip()}
    if isinstance(raw, str) and raw.strip():
        return {s.strip() for s in raw.split(",") if s.strip()}
    return {"prospective", "events"}


class _EnrichmentWritebackBase(BaseHandler):
    """Shared cortex dispatch + model call helpers for enrichment steps."""

    field_name: str = ""
    kind_name: str = ""

    async def _load_assertion(self, client: Any, assertion_id: int) -> dict[str, Any]:
        return await self._dispatch(client, "assertion_get", {"assertion_id": assertion_id})

    @staticmethod
    async def _dispatch(
        client: Any, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            resp = await client.post(
                "/dispatch", json={"tool": tool, "arguments": arguments}
            )
        except Exception as exc:
            logger.warning("cortex dispatch %s failed: %s", tool, exc)
            return {"error": f"transport_error: {exc}"}
        if resp.status_code >= 400:
            try:
                body = resp.json()
                if isinstance(body, dict):
                    return (
                        body if "error" in body else {"error": json.dumps(body)[:300]}
                    )
            except Exception:
                pass
            return {"error": f"http_{resp.status_code}: {resp.text[:300]}"}
        try:
            data = resp.json()
        except Exception as exc:
            return {"error": f"invalid_json_response: {exc}"}
        return data if isinstance(data, dict) else {"error": "non_object_response"}

    async def _write_field(
        self, client: Any, assertion_id: int, value: str
    ) -> dict[str, Any]:
        return await self._dispatch(
            client,
            "assertion_update",
            {"assertion_id": assertion_id, self.field_name: value},
        )

    async def _run_llm(
        self, step: Any, context: Any, template_vars: dict[str, str]
    ) -> tuple[str | None, str | None]:
        rendered = self._render_prompt(step.prompt_ref, template_vars, context)
        model_id = self._resolve_model_alias(step.model_ref, context)
        gen = step.generation_parameters or {}
        llm_result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=gen.get("temperature", 0.3),
            max_tokens=gen.get("max_tokens", 256),
        )
        content = (llm_result.content or "").strip()
        if not content:
            return None, "model returned empty content"
        return content, None

    async def _execute_common(
        self, step: Any, context: Any, *, parse_value: Any
    ) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        assertion_id = opts.get("assertion_id")
        claim = opts.get("claim")
        entity_id = opts.get("entity_id")
        confidence = opts.get("confidence") or "believed"

        if not isinstance(assertion_id, int) or not claim:
            err = "missing required pipeline_options: assertion_id (int), claim (str)"
            return _step_output({"ok": False, "error": err}, error=err)

        if self.kind_name not in _kinds_enabled(context):
            return _step_output(
                {
                    "ok": True,
                    "skipped": True,
                    "assertion_id": assertion_id,
                    "reason": f"kind {self.kind_name!r} not enabled",
                }
            )

        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_REQUEST_TIMEOUT
        ) as client:
            existing = await self._load_assertion(client, assertion_id)
            if "error" in existing:
                err = existing["error"]
                return _step_output(
                    {"ok": False, "assertion_id": assertion_id, "error": err},
                    error=err,
                )
            if existing.get(self.field_name):
                return _step_output(
                    {
                        "ok": True,
                        "skipped": True,
                        "assertion_id": assertion_id,
                        self.field_name: existing[self.field_name],
                        "reason": f"{self.field_name} already populated",
                    }
                )

            raw, llm_err = await self._run_llm(
                step,
                context,
                {
                    "entity_id": entity_id or "unknown",
                    "claim": claim,
                    "confidence": confidence,
                },
            )
            if llm_err or raw is None:
                err = llm_err or "empty model output"
                return _step_output(
                    {"ok": False, "assertion_id": assertion_id, "error": err},
                    error=err,
                )

            parsed, parse_err = parse_value(raw)
            if parse_err:
                return _step_output(
                    {"ok": False, "assertion_id": assertion_id, "error": parse_err},
                    error=parse_err,
                )

            update = await self._write_field(client, assertion_id, parsed)
            if "error" in update:
                err = update["error"]
                return _step_output(
                    {
                        "ok": False,
                        "assertion_id": assertion_id,
                        self.field_name: parsed,
                        "error": err,
                    },
                    error=err,
                )

        logger.info(
            "assertion_enrichment: assertion=%d field=%s",
            assertion_id,
            self.field_name,
        )
        return _step_output(
            {"ok": True, "assertion_id": assertion_id, self.field_name: parsed}
        )


class AssertionEnrichmentProspectiveHandler(_EnrichmentWritebackBase):
    step_type = "assertion_enrichment_prospective_v1"
    field_name = "prospective_summary"
    kind_name = "prospective"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        return await self._execute_common(step, context, parse_value=_passthrough_text)


class AssertionEnrichmentEventsHandler(_EnrichmentWritebackBase):
    step_type = "assertion_enrichment_events_v1"
    field_name = "events_json"
    kind_name = "events"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        return await self._execute_common(step, context, parse_value=_parse_events_json)


def _passthrough_text(raw: str) -> tuple[str | None, str | None]:
    text = raw.strip()
    if not text:
        return None, "empty prospective summary"
    return text, None


def _parse_events_json(raw: str) -> tuple[str | None, str | None]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = [
            line for line in cleaned.split("\n") if not line.strip().startswith("```")
        ]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None, f"invalid events JSON: {cleaned[:200]}"
    if not isinstance(parsed, list):
        return None, f"events JSON must be an array, got {type(parsed).__name__}"
    return json.dumps(parsed), None
