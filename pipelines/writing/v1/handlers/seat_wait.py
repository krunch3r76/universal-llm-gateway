"""Wait for a seat admit and choose the seat or local fallback.

``writing_seat_wait_v1`` polls until the admit is terminal or the seat
timeout elapses. ``writing_seat_select_v1`` prefers the wait result, then
the local generate step. Neither handler sets ``StepOutput.error``.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_STARGATE_URL,
    make_async_client,
)

from .assemble import openai_reviewer_quota_reason
from .seat_dispatch import _held_openai_quota_step, _role, _thread_id, _timeout

_WAIT_SLICE = 55.0

_PREAMBLE = re.compile(
    r"^(?:here(?:'s| is) the draft:|sure,? here(?:'s| is)(?: the message)?:)\s*",
    re.IGNORECASE,
)
_FENCE_BLOCK = re.compile(r"^```(?:\w+)?\s*([\s\S]*?)\s*```$", re.MULTILINE)
_DRAFT_TAG = re.compile(r"^<draft>\s*([\s\S]*?)\s*</draft>$", re.IGNORECASE)


def sanitize_plain_text_draft(raw_text: str) -> str:
    """Strip fences, draft tags, and conversational preambles."""
    text = str(raw_text or "").strip()
    if not text:
        return ""
    match = _FENCE_BLOCK.search(text)
    if match:
        text = match.group(1).strip()
    tag = _DRAFT_TAG.match(text)
    if tag:
        text = tag.group(1).strip()
    text = _PREAMBLE.sub("", text, count=1).strip()
    return text


def _step(payload: dict[str, Any], *, model_id: str | None = None) -> StepOutput:
    return StepOutput(
        raw=json.dumps(payload), json=payload, error=None, model_id=model_id
    )


def _json_of(outputs: dict[str, Any], name: str) -> dict[str, Any] | None:
    step = outputs.get(name)
    if step is None:
        return None
    data = step.get("json") if isinstance(step, dict) else getattr(step, "json", None)
    if isinstance(data, dict) and data.get("_skipped") is True:
        return None
    return data if isinstance(data, dict) else None


def _raw(outputs: dict[str, Any], name: str) -> Any:
    step = outputs.get(name)
    data = _json_of(outputs, name)
    if data is None and step is not None:
        skipped = (
            step.get("json") if isinstance(step, dict) else getattr(step, "json", None)
        )
        if isinstance(skipped, dict) and skipped.get("_skipped") is True:
            return None
    return step


def _first_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


_DISPOSITIONS = frozenset({"express", "imply", "omit_with_reason", "request"})
# Fix text that concedes the sentence is fine is not an actionable finding.
_CONCESSION = (
    "is supported",
    "supported by the documents",
    "acceptable",
    "consistent with",
)


_PRESERVE_FINDING_TYPES = frozenset(
    {
        "not_purpose_relevant",
        "intake_answer_promoted",
        "volunteered_negative_or_concession",
        "prior_draft_carryover",
    }
)


def findings_actionable(findings: Any) -> list[Any]:
    """Drop findings whose fix text concedes the sentence.

    The list is the documented concession phrases, matched case-insensitively
    as substrings. Provenance violations are not findings and are not filtered.
    """
    if not isinstance(findings, list):
        return []
    kept: list[Any] = []
    for row in findings:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        if str(row.get("type") or "") in _PRESERVE_FINDING_TYPES:
            kept.append(row)
            continue
        fix = str(row.get("fix") or "").lower()
        if any(phrase in fix for phrase in _CONCESSION):
            continue
        kept.append(row)
    return kept


def _valid(role: str, obj: dict[str, Any] | None) -> bool:
    if not isinstance(obj, dict):
        return False
    if role == "writer":
        if not (
            isinstance(obj.get("draft"), str)
            and isinstance(obj.get("claims"), list)
            and isinstance(obj.get("need"), list)
            and isinstance(obj.get("dispositions"), list)
        ):
            return False
        for claim in obj["claims"]:
            if not isinstance(claim, dict):
                continue
            disposition = claim.get("disposition")
            if disposition is None:
                continue
            if str(disposition) not in _DISPOSITIONS:
                return False
        return True
    from .review_schema import review_result_error

    return review_result_error(obj) is None


def _result_text(data: dict[str, Any]) -> str:
    for key in ("result_text", "output", "text", "content"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val
    result = data.get("result")
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("content", "text", "output", "raw"):
            value = result.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(result)
    return json.dumps(data)


def _fallback_to(role: str, assemble: dict[str, Any]) -> str | None:
    if role == "writer":
        return "local"
    if assemble.get("sensitivity") == "non_sensitive":
        return "local"
    return None


def _fail(
    *,
    reason: str,
    seat: Any,
    role: str,
    execution_id: Any,
    key: Any,
    waited: float,
    fallback: str | None,
) -> StepOutput:
    record = {
        "seat": seat,
        "role": role,
        "reason": reason,
        "execution_id": execution_id,
        "idempotency_key": key,
        "waited_s": waited,
        "fallback_to": fallback,
    }
    return _step({"ok": False, "fallback_to": fallback, "seat_fallback": record})


class WritingSeatWaitHandler(BaseHandler):
    """Poll an admitted seat until terminal, timeout, or a typed failure.

    CDP uses ``GET /api/v1/executions/{id}``. cursor_pool1 follows the
    agent-bus poll_hint (the execution record does not carry SDK text).
    Clock and sleep are injectable so tests do not wait.
    """

    step_type = "writing_seat_wait_v1"

    def __init__(
        self,
        client: Any | None = None,
        bus: Any | None = None,
        *,
        now: Any | None = None,
        sleep: Any | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._bus = bus
        self._now = now or time.monotonic
        self._sleep = sleep

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Return the parsed seat JSON or a fallback record. Never sets error.

        A dispatch that was not admitted fails with no HTTP call. A deadline
        records ``reason=timeout`` and does not cancel the remote execution.
        """
        role = _role(step)
        options = getattr(context, "options", {}) or {}
        outputs = getattr(context, "outputs", {}) or {}
        timeout_s = _timeout(options)
        assemble = _json_of(outputs, "assemble") or {}
        dispatch_name = "draft_dispatch" if role == "writer" else "review_dispatch"
        dispatch = _json_of(outputs, dispatch_name) or {}
        seat = dispatch.get("seat") or assemble.get(
            "writer_seat" if role == "writer" else "reviewer_seat"
        )
        key = dispatch.get("idempotency_key")
        execution_id = dispatch.get("execution_id")
        fallback = _fallback_to(role, assemble)
        if dispatch.get("refused"):
            return _fail(
                reason="refused",
                seat=seat,
                role=role,
                execution_id=execution_id,
                key=key,
                waited=0,
                fallback=fallback,
            )
        if dispatch.get("duplicate_suppressed"):
            return _fail(
                reason="duplicate_suppressed",
                seat=seat,
                role=role,
                execution_id=execution_id,
                key=key,
                waited=0,
                fallback=fallback,
            )
        admit = dispatch.get("admit")
        if admit == "failed":
            return _fail(
                reason="admit_failed",
                seat=seat,
                role=role,
                execution_id=execution_id,
                key=key,
                waited=0,
                fallback=fallback,
            )
        if admit == "unknown" or admit != "admitted":
            return _fail(
                reason="admit_unknown",
                seat=seat,
                role=role,
                execution_id=execution_id,
                key=key,
                waited=0,
                fallback=fallback,
            )
        started = self._now()
        deadline = started + timeout_s
        if str(seat) == "cursor_pool1":
            return await self._wait_bus(
                dispatch,
                role=role,
                seat=seat,
                key=key,
                execution_id=execution_id,
                fallback=fallback,
                started=started,
                deadline=deadline,
            )
        return await self._wait_execution(
            role=role,
            seat=seat,
            key=key,
            execution_id=execution_id,
            fallback=fallback,
            started=started,
            deadline=deadline,
        )

    async def _sleep_round(self, round_index: int, deadline: float) -> None:
        delay = float(min(2**round_index, 10))
        remaining = deadline - self._now()
        if remaining <= 0:
            return
        pause = min(delay, remaining)
        if self._sleep is not None:
            await self._sleep(pause)
        else:
            import asyncio

            await asyncio.sleep(pause)

    async def _wait_execution(
        self,
        *,
        role: str,
        seat: Any,
        key: Any,
        execution_id: Any,
        fallback: str | None,
        started: float,
        deadline: float,
    ) -> StepOutput:
        if not execution_id:
            return _fail(
                reason="admit_unknown",
                seat=seat,
                role=role,
                execution_id=execution_id,
                key=key,
                waited=self._now() - started,
                fallback=fallback,
            )
        round_index = 0
        own_client = self._client is None
        client = self._client
        if own_client:
            slice_s = min(_WAIT_SLICE, max(deadline - self._now(), 0.1))
            client_cm = make_async_client(DEFAULT_STARGATE_URL, timeout=slice_s + 10)
            client = await client_cm.__aenter__()
        try:
            while self._now() < deadline:
                slice_s = min(_WAIT_SLICE, deadline - self._now())
                if slice_s <= 0:
                    break
                try:
                    resp = await client.get(
                        f"/api/v1/executions/{execution_id}",
                        params={"wait": slice_s},
                    )
                except Exception:
                    await self._sleep_round(round_index, deadline)
                    round_index += 1
                    continue
                status_code = getattr(resp, "status_code", None)
                if (
                    isinstance(status_code, int)
                    and 400 <= status_code < 500
                    and status_code not in (408, 429)
                ):
                    return _fail(
                        reason=f"poll_http_{status_code}",
                        seat=seat,
                        role=role,
                        execution_id=execution_id,
                        key=key,
                        waited=self._now() - started,
                        fallback=fallback,
                    )
                if isinstance(status_code, int) and (
                    status_code >= 500 or status_code in (408, 429)
                ):
                    await self._sleep_round(round_index, deadline)
                    round_index += 1
                    continue
                try:
                    data = resp.json() if getattr(resp, "content", True) else {}
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                status = str(data.get("status") or "")
                if status in {"failed", "cancelled", "canceled"}:
                    return _fail(
                        reason="execution_failed",
                        seat=seat,
                        role=role,
                        execution_id=execution_id,
                        key=key,
                        waited=self._now() - started,
                        fallback=fallback,
                    )
                if status in {"completed", "complete", "succeeded", "success"}:
                    parsed = _first_json(_result_text(data))
                    if not _valid(role, parsed):
                        return _fail(
                            reason="unparseable",
                            seat=seat,
                            role=role,
                            execution_id=execution_id,
                            key=key,
                            waited=self._now() - started,
                            fallback=fallback,
                        )
                    assert parsed is not None
                    return _step(
                        {
                            "ok": True,
                            "seat": seat,
                            "model_id": None,
                            "execution_id": execution_id,
                            "idempotency_key": key,
                            "waited_s": self._now() - started,
                            "result": parsed,
                        }
                    )
                await self._sleep_round(round_index, deadline)
                round_index += 1
        finally:
            if own_client:
                await client_cm.__aexit__(None, None, None)
        return _fail(
            reason="timeout",
            seat=seat,
            role=role,
            execution_id=execution_id,
            key=key,
            waited=self._now() - started,
            fallback=fallback,
        )

    async def _wait_bus(
        self,
        dispatch: dict[str, Any],
        *,
        role: str,
        seat: Any,
        key: Any,
        execution_id: Any,
        fallback: str | None,
        started: float,
        deadline: float,
    ) -> StepOutput:
        if not execution_id:
            return _fail(
                reason="admit_unknown",
                seat=seat,
                role=role,
                execution_id=execution_id,
                key=key,
                waited=self._now() - started,
                fallback=fallback,
            )
        hint = dispatch.get("poll_hint")
        args = hint.get("arguments") if isinstance(hint, dict) else {}
        if not isinstance(args, dict):
            args = {}
        thread = str(
            args.get("thread") or dispatch.get("dispatch_thread_id") or ""
        ).strip()
        after = args.get("after_turn") if isinstance(args.get("after_turn"), int) else 0
        round_index = 0
        own = self._bus is None
        bus = self._bus
        if own:
            bus_cm = make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_WAIT_SLICE + 10)
            bus = await bus_cm.__aenter__()
        try:
            while self._now() < deadline:
                try:
                    resp = await bus.get(
                        f"/threads/{thread}/wait",
                        params={
                            "from_agent": "cursor-sdk",
                            "wait": int(min(_WAIT_SLICE, deadline - self._now())),
                            "after_turn": after,
                            "mark_read": "false",
                        },
                    )
                except Exception:
                    await self._sleep_round(round_index, deadline)
                    round_index += 1
                    continue
                status_code = getattr(resp, "status_code", None)
                if (
                    isinstance(status_code, int)
                    and 400 <= status_code < 500
                    and status_code not in (408, 429)
                ):
                    return _fail(
                        reason=f"poll_http_{status_code}",
                        seat=seat,
                        role=role,
                        execution_id=execution_id,
                        key=key,
                        waited=self._now() - started,
                        fallback=fallback,
                    )
                if isinstance(status_code, int) and (
                    status_code >= 500 or status_code in (408, 429)
                ):
                    await self._sleep_round(round_index, deadline)
                    round_index += 1
                    continue
                try:
                    snap = resp.json() if getattr(resp, "content", True) else {}
                except Exception:
                    snap = {}
                if not isinstance(snap, dict):
                    snap = {}
                status = str(snap.get("status") or "")
                producer = (
                    snap.get("producer")
                    if isinstance(snap.get("producer"), dict)
                    else {}
                )
                if status == "producer_terminal" or (
                    status in {"no_new_turn", "predicate_unmet"}
                    and str(producer.get("state") or "") == "terminal"
                ):
                    return _fail(
                        reason="execution_failed",
                        seat=seat,
                        role=role,
                        execution_id=execution_id,
                        key=key,
                        waited=self._now() - started,
                        fallback=fallback,
                    )
                if status == "complete":
                    turn = snap.get("qualifying_reply_turn")
                    body = ""
                    if isinstance(turn, int):
                        try:
                            got = await bus.get(
                                "/turns/by-number",
                                params={"thread": thread, "turn_number": str(turn)},
                            )
                            got_status = getattr(got, "status_code", None)
                            if (
                                isinstance(got_status, int)
                                and 400 <= got_status < 500
                                and got_status not in (408, 429)
                            ):
                                return _fail(
                                    reason=f"poll_http_{got_status}",
                                    seat=seat,
                                    role=role,
                                    execution_id=execution_id,
                                    key=key,
                                    waited=self._now() - started,
                                    fallback=fallback,
                                )
                            if isinstance(got_status, int) and (
                                got_status >= 500 or got_status in (408, 429)
                            ):
                                await self._sleep_round(round_index, deadline)
                                round_index += 1
                                continue
                            payload = (
                                got.json() if getattr(got, "content", True) else {}
                            )
                        except Exception:
                            await self._sleep_round(round_index, deadline)
                            round_index += 1
                            continue
                        if isinstance(payload, dict):
                            body = str(payload.get("body") or payload.get("text") or "")
                    parsed = _first_json(body)
                    if not _valid(role, parsed):
                        return _fail(
                            reason="unparseable",
                            seat=seat,
                            role=role,
                            execution_id=execution_id,
                            key=key,
                            waited=self._now() - started,
                            fallback=fallback,
                        )
                    assert parsed is not None
                    return _step(
                        {
                            "ok": True,
                            "seat": seat,
                            "model_id": None,
                            "execution_id": execution_id,
                            "idempotency_key": key,
                            "waited_s": self._now() - started,
                            "result": parsed,
                        }
                    )
                await self._sleep_round(round_index, deadline)
                round_index += 1
        finally:
            if own:
                await bus_cm.__aexit__(None, None, None)
        return _fail(
            reason="timeout",
            seat=seat,
            role=role,
            execution_id=execution_id,
            key=key,
            waited=self._now() - started,
            fallback=fallback,
        )


def _copy_usage(out: StepOutput, local: Any) -> StepOutput:
    out.model_id = getattr(local, "model_id", None)
    out.prompt_tokens = int(getattr(local, "prompt_tokens", 0) or 0)
    out.completion_tokens = int(getattr(local, "completion_tokens", 0) or 0)
    out.model_call_count = int(getattr(local, "model_call_count", 0) or 0)
    return out


class WritingReviewOpenaiWaitHandler(BaseHandler):
    """Poll an OpenAI reviewer admit until terminal, quota-held, or timeout."""

    step_type = "writing_review_openai_wait_v1"

    def __init__(
        self,
        client: Any | None = None,
        bus_client: Any | None = None,
        *,
        now: Any | None = None,
        sleep: Any | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._bus_client = bus_client
        self._now = now or time.monotonic
        self._sleep = sleep

    async def _sleep_round(self, round_index: int, deadline: float) -> None:
        delay = float(min(2**round_index, 10))
        remaining = deadline - self._now()
        if remaining <= 0:
            return
        pause = min(delay, remaining)
        if self._sleep is not None:
            await self._sleep(pause)
        else:
            import asyncio

            await asyncio.sleep(pause)

    async def execute(self, step: Any, context: Any) -> StepOutput:
        del step
        options = getattr(context, "options", {}) or {}
        outputs = getattr(context, "outputs", {}) or {}
        timeout_s = _timeout(options)
        assemble = _json_of(outputs, "assemble") or {}
        dispatch = _json_of(outputs, "review_openai") or {}
        writer_model = str(assemble.get("writer_model") or "")
        reviewer_model = str(
            dispatch.get("model") or assemble.get("reviewer_model") or ""
        )
        thread = _thread_id(options)
        execution_id = dispatch.get("execution_id")
        key = dispatch.get("idempotency_key")
        if dispatch.get("status") == "held_reviewer_unfunded":
            return _step(dispatch)
        if dispatch.get("ok") is not True or dispatch.get("admit") != "admitted":
            return _step(
                {
                    "ok": False,
                    "refused": "review_transport_failed",
                    "error": dispatch.get("error") or "admit_failed",
                }
            )
        if not execution_id:
            return _step(
                {
                    "ok": False,
                    "refused": "review_transport_failed",
                    "error": "missing_execution_id",
                }
            )
        started = self._now()
        deadline = started + timeout_s
        round_index = 0
        own_client = self._client is None
        client = self._client
        if own_client:
            slice_s = min(_WAIT_SLICE, max(deadline - self._now(), 0.1))
            client_cm = make_async_client(DEFAULT_STARGATE_URL, timeout=slice_s + 10)
            client = await client_cm.__aenter__()
        try:
            while self._now() < deadline:
                slice_s = min(_WAIT_SLICE, deadline - self._now())
                if slice_s <= 0:
                    break
                try:
                    resp = await client.get(
                        f"/api/v1/executions/{execution_id}",
                        params={"wait": slice_s},
                    )
                except Exception:
                    await self._sleep_round(round_index, deadline)
                    round_index += 1
                    continue
                status_code = getattr(resp, "status_code", None)
                if (
                    isinstance(status_code, int)
                    and 400 <= status_code < 500
                    and status_code not in (408, 429)
                ):
                    return _step(
                        {
                            "ok": False,
                            "refused": "review_transport_failed",
                            "error": f"poll_http_{status_code}",
                        }
                    )
                if isinstance(status_code, int) and (
                    status_code >= 500 or status_code in (408, 429)
                ):
                    await self._sleep_round(round_index, deadline)
                    round_index += 1
                    continue
                try:
                    data = resp.json() if getattr(resp, "content", True) else {}
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                status = str(data.get("status") or "")
                if status in {"failed", "cancelled", "canceled"}:
                    quota_reason = openai_reviewer_quota_reason(data)
                    if quota_reason:
                        return await _held_openai_quota_step(
                            thread=thread,
                            writer_model=writer_model,
                            reviewer_model=reviewer_model,
                            reason=quota_reason,
                            bus_client=self._bus_client,
                        )
                    return _step(
                        {
                            "ok": False,
                            "refused": "review_transport_failed",
                            "error": "execution_failed",
                            "execution_id": execution_id,
                            "waited_s": self._now() - started,
                        }
                    )
                if status in {"completed", "complete", "succeeded", "success"}:
                    parsed = _first_json(_result_text(data))
                    if not _valid("reviewer", parsed):
                        return _step(
                            {
                                "ok": False,
                                "refused": "review_transport_failed",
                                "error": "unparseable",
                                "execution_id": execution_id,
                                "waited_s": self._now() - started,
                            }
                        )
                    assert parsed is not None
                    payload = dict(parsed)
                    payload["ok"] = True
                    payload["seat_record"] = {
                        "seat": "openai",
                        "model_id": reviewer_model,
                        "execution_id": execution_id,
                        "idempotency_key": key,
                    }
                    payload["findings_actionable"] = findings_actionable(
                        payload.get("findings")
                    )
                    return _step(payload, model_id=reviewer_model)
                await self._sleep_round(round_index, deadline)
                round_index += 1
        finally:
            if own_client:
                await client_cm.__aexit__(None, None, None)
        return _step(
            {
                "ok": False,
                "refused": "review_transport_failed",
                "error": "timeout",
                "execution_id": execution_id,
                "waited_s": self._now() - started,
            }
        )


class WritingSeatSelectHandler(BaseHandler):
    """Choose the wait result, else the local generate step, else a refusal.

    A skipped step (``_skipped``) counts as absent. The local branch copies
    token fields so finalize usage still sums. ``StepOutput.error`` stays empty.
    """

    step_type = "writing_seat_select_v1"

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Fold the role's wait and local outputs into one draft or review JSON.

        Wait success wins. A failed wait with a good local step carries
        ``seat_fallback``. Both missing refuses the role.
        """
        role = _role(step)
        outputs = getattr(context, "outputs", {}) or {}
        if role == "reviewer":
            for step_name in ("review_openai_wait", "review_openai"):
                openai_review = _json_of(outputs, step_name)
                if not isinstance(openai_review, dict):
                    continue
                if openai_review.get("status") == "held_reviewer_unfunded":
                    return _step(openai_review)
                if openai_review.get("ok") is True and openai_review.get("verdict") in {
                    "ship",
                    "revise",
                }:
                    payload = dict(openai_review)
                    if "findings_actionable" not in payload:
                        payload["findings_actionable"] = findings_actionable(
                            payload.get("findings")
                        )
                    record = payload.get("seat_record")
                    model = (
                        record.get("model_id")
                        if isinstance(record, dict)
                        else openai_review.get("model")
                    )
                    return _step(payload, model_id=str(model) if model else None)
                if openai_review.get("refused") == "review_transport_failed":
                    return _step(
                        {
                            "refused": "review_transport_failed",
                            "reason": "review_transport_failed",
                            "error": openai_review.get("error")
                            or "review_transport_failed",
                        }
                    )
        wait_name = "draft_wait" if role == "writer" else "review_wait"
        local_name = "draft_local" if role == "writer" else "review_local"
        waited = _json_of(outputs, wait_name)
        local = _raw(outputs, local_name)
        local_json = _json_of(outputs, local_name)
        local_error = getattr(local, "error", None) if local is not None else None
        if isinstance(local, dict):
            local_error = local.get("error")
        if (
            waited
            and waited.get("ok") is True
            and isinstance(waited.get("result"), dict)
        ):
            result = dict(waited["result"])
            result["seat_record"] = {
                "seat": waited.get("seat"),
                "model_id": waited.get("model_id"),
                "execution_id": waited.get("execution_id"),
                "idempotency_key": waited.get("idempotency_key"),
            }
            model = waited.get("model_id")
            if not model:
                record_model = None
                dispatch = _json_of(
                    outputs, "draft_dispatch" if role == "writer" else "review_dispatch"
                )
                if dispatch:
                    record_model = dispatch.get("model")
                model = record_model
            if model:
                result["seat_record"]["model_id"] = model
            if role == "reviewer":
                result["findings_actionable"] = findings_actionable(
                    result.get("findings")
                )
            return _step(result, model_id=str(model) if model else None)
        fallback = waited.get("seat_fallback") if isinstance(waited, dict) else None
        if (
            isinstance(local_json, dict)
            and local_json.get("refused") == "review_transport_failed"
        ):
            return _step(
                {
                    "refused": "review_transport_failed",
                    "reason": "review_transport_failed",
                    "error": local_json.get("error") or "review_transport_failed",
                    "seat_fallback": fallback,
                }
            )
        if local is not None and not local_error and isinstance(local_json, dict):
            if not _valid(role, local_json):
                refused = (
                    "writer_seat_failed" if role == "writer" else "reviewer_seat_failed"
                )
                return _step(
                    {
                        "refused": refused,
                        "reason": "local_result_invalid",
                        "seat_fallback": fallback,
                        "error": refused,
                    }
                )
            payload = dict(local_json)
            payload["seat_record"] = {
                "seat": "local",
                "model_id": getattr(local, "model_id", None),
            }
            if role == "reviewer":
                payload["findings_actionable"] = findings_actionable(
                    payload.get("findings")
                )
            if fallback:
                payload["seat_fallback"] = fallback
            return _copy_usage(_step(payload), local)
        refused = "writer_seat_failed" if role == "writer" else "reviewer_seat_failed"
        return _step(
            {
                "refused": refused,
                "seat_fallback": fallback,
                "error": refused,
            }
        )
