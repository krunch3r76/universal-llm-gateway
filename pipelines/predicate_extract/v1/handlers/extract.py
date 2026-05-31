"""Predicate-extract handler — idempotent peer-projection writer.

v2.4 Slice 3 §6.7. Triggered fire-and-forget by cortex-api on assertion
insert; pipeline_options carry `assertion_id`, `claim`, `entity_id`.

Sequence:
  1. cortex-api dispatch `assertion_get`:
        - missing → error.
        - predicate_form already non-null → skip (idempotent re-run path).
  2. T2 model call (qwen3-14b-q4-k-m-40960 via `model_ref: t2`):
        natural-language claim → single-line predicate(subject, object[, ...]).
  3. cortex-api dispatch `assertion_update` with `predicate_form=<rendered>`.

§5.5.4 cache key is collapsed to `(assertion_id, predicate_form IS NULL)`
because assertions are immutable per row (supersede creates new ids); the
content_hash and edge_state_hash components are over-keyed for a
claim-text-only projection (web ratification, agent-bus thread 904
turn 2 — see assertion 8526).
"""

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


class PredicateExtractApplyHandler(BaseHandler):
    """Single-step idempotent predicate-form extractor + writeback."""

    step_type = "predicate_extract_apply_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        assertion_id = opts.get("assertion_id")
        claim = opts.get("claim")
        entity_id = opts.get("entity_id")

        if not isinstance(assertion_id, int) or not claim:
            err = "missing required pipeline_options: assertion_id (int), claim (str)"
            return _step_output({"ok": False, "error": err}, error=err)

        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_REQUEST_TIMEOUT
        ) as client:
            existing = await self._dispatch(
                client, "assertion_get", {"assertion_id": assertion_id}
            )
            if "error" in existing:
                err = existing["error"]
                return _step_output(
                    {"ok": False, "assertion_id": assertion_id, "error": err},
                    error=err,
                )
            if existing.get("predicate_form"):
                payload = {
                    "ok": True,
                    "skipped": True,
                    "assertion_id": assertion_id,
                    "predicate_form": existing["predicate_form"],
                    "reason": "predicate_form already populated",
                }
                return _step_output(payload)

            rendered = self._render_prompt(
                step.prompt_ref,
                {"entity_id": entity_id or "unknown", "claim": claim},
                context,
            )
            model_id = self._resolve_model_alias(step.model_ref, context)
            gen = step.generation_parameters or {}
            llm_result = await self._call_model(
                model_id,
                rendered.user_prompt,
                step,
                context,
                system_prompt=rendered.system_prompt,
                temperature=gen.get("temperature", 0.1),
                max_tokens=gen.get("max_tokens", 128),
            )
            predicate_form = self._first_line(llm_result.content)
            if not predicate_form:
                err = "model returned empty predicate_form"
                return _step_output(
                    {"ok": False, "assertion_id": assertion_id, "error": err},
                    error=err,
                )

            update = await self._dispatch(
                client,
                "assertion_update",
                {"assertion_id": assertion_id, "predicate_form": predicate_form},
            )
            if "error" in update:
                err = update["error"]
                return _step_output(
                    {
                        "ok": False,
                        "assertion_id": assertion_id,
                        "predicate_form": predicate_form,
                        "error": err,
                    },
                    error=err,
                )

        logger.info(
            "predicate_extract: assertion=%d form=%r", assertion_id, predicate_form
        )
        return _step_output(
            {
                "ok": True,
                "assertion_id": assertion_id,
                "predicate_form": predicate_form,
            }
        )

    @staticmethod
    def _first_line(text: str | None) -> str:
        if not text:
            return ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @staticmethod
    async def _dispatch(
        client: Any, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """POST /dispatch → dict; structured `{"error": ...}` on any failure."""
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
