"""Densify hop — optional cursor-sdk investigate before compose writes the paste file."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    DEFAULT_STARGATE_URL,
    make_async_client,
)

from ._cortex import cortex_dispatch
from ._message import (
    MAESTRO_MEMO_THREAD,
    densify_ask_prompt,
    densify_dispatch_body,
    densify_sdk_model,
    extract_densify_splice,
    parse_compose_options,
)
from .launch import agent_bus_headers, cursor_sdk_refuse_payload, post_json

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30.0
_WAIT_TIMEOUT = 55.0
_WAIT_ROUNDS = 24
DensifyHop = Callable[..., Awaitable[dict[str, Any]]]


def _step(payload: dict[str, Any], *, error: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload, default=str), json=payload, error=error)


class CursorPasteDensifyHandler(BaseHandler):
    step_type = "cursor_paste_resolve_densify_v1"
    hop: DensifyHop | None = None

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        bound = parse_compose_options(opts)
        if isinstance(bound, str):
            return _step({"ok": False, "error": bound}, error=bound)
        densify = bound["densify"]
        if not densify:
            return _step(
                {
                    "ok": True,
                    "skipped": True,
                    "splice": "",
                    "model": "",
                    "densify": "",
                }
            )
        model = densify_sdk_model(densify)
        if not model:
            err = f"unknown densify fold {densify!r}"
            return _step({"ok": False, "error": err}, error=err)

        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_REQUEST_TIMEOUT
        ) as client:
            row = await cortex_dispatch(
                client, "assertion_get", {"assertion_id": bound["assertion_id"]}
            )
        if not isinstance(row, dict) or "error" in row:
            err = row.get("error") if isinstance(row, dict) else "assertion_get failed"
            if not err:
                err = f"assertion_get did not return id {bound['assertion_id']}"
            return _step(
                {
                    "ok": False,
                    "assertion_id": bound["assertion_id"],
                    "error": str(err),
                },
                error=str(err),
            )

        prompt = densify_ask_prompt(bound["kind"], bound["assertion_id"], row)
        hop = self.hop if self.hop is not None else default_densify_hop
        result = await hop(bound=bound, model=model, prompt=prompt)
        if not result.get("ok"):
            err = str(result.get("error") or "densify hop failed")
            payload = {
                "ok": False,
                "error": err,
                "http_status": result.get("http_status") or 422,
                "model": model,
                "densify": densify,
                "dispatch": result.get("dispatch"),
            }
            return _step(payload, error=err)
        splice = str(result.get("splice") or "").strip()
        if not splice:
            err = "densify hop returned an empty splice"
            return _step(
                {
                    "ok": False,
                    "error": err,
                    "http_status": 422,
                    "model": model,
                    "dispatch": result.get("dispatch"),
                },
                error=err,
            )
        return _step(
            {
                "ok": True,
                "skipped": False,
                "densify": densify,
                "model": model,
                "splice": splice,
                "dispatch_thread_id": result.get("dispatch_thread_id"),
                "dispatch": result.get("dispatch"),
            }
        )


async def default_densify_hop(
    *,
    bound: dict[str, Any],
    model: str,
    prompt: str,
) -> dict[str, Any]:
    """Admit investigate, wait for the SDK closeout, extract the CRANE splice.

    A 422 (including Fable house block) is quoted and not swapped to ``cdp/fable``.
    """
    kind = bound["kind"]
    assertion_id = bound["assertion_id"]
    thread_id = str(bound.get("densify_thread_id") or "").strip()
    if thread_id == MAESTRO_MEMO_THREAD:
        return {
            "ok": False,
            "http_status": 422,
            "error": "dispatch_thread_id 12286 is maestro memo only — mint a work/review thread",
        }

    headers = agent_bus_headers()
    if not thread_id:
        if headers is None:
            payload = cursor_sdk_refuse_payload(
                reason="AGENT_BUS_TOKEN unset — cannot mint a densify thread",
                admit={"model": model, "job": "investigate"},
            )
            return {
                "ok": False,
                "http_status": 422,
                "error": payload["error"],
            }
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL, timeout=_REQUEST_TIMEOUT
        ) as bus:
            slug = f"cursor-paste-densify-{kind}-{assertion_id}"
            mint = await post_json(
                bus,
                "/threads",
                {"slug": slug, "idempotency_key": slug},
                headers=headers,
            )
            if "error" in mint:
                return {
                    "ok": False,
                    "http_status": mint.get("http_status") or 422,
                    "error": f"create_thread failed: {mint['error']}",
                    "dispatch": mint,
                }
            thread_id = str(mint.get("id") or "")
            if not thread_id:
                return {
                    "ok": False,
                    "http_status": 422,
                    "error": "create_thread returned no id",
                    "dispatch": mint,
                }

    body = densify_dispatch_body(
        kind=kind,
        assertion_id=assertion_id,
        prompt=prompt,
        dispatch_thread_id=thread_id,
        model=model,
    )
    async with make_async_client(
        DEFAULT_STARGATE_URL, timeout=_REQUEST_TIMEOUT
    ) as stargate:
        dispatched = await post_json(stargate, "/api/v1/team/dispatch", body)
    if dispatched.get("error") or (
        isinstance(dispatched.get("status_code"), int)
        and dispatched["status_code"] >= 400
    ):
        err = dispatched.get("error") or dispatched
        return {
            "ok": False,
            "http_status": dispatched.get("http_status") or 422,
            "error": str(err)[:500],
            "dispatch": dispatched,
            "dispatch_thread_id": thread_id,
        }

    closeout = await _wait_sdk_closeout(thread_id, headers=headers)
    if not closeout.get("ok"):
        return {
            "ok": False,
            "http_status": closeout.get("http_status") or 422,
            "error": closeout.get("error") or "densify wait failed",
            "dispatch": dispatched,
            "dispatch_thread_id": thread_id,
        }
    splice = extract_densify_splice(str(closeout.get("body") or ""))
    if not splice:
        return {
            "ok": False,
            "http_status": 422,
            "error": "densify hop returned an empty splice",
            "dispatch": dispatched,
            "dispatch_thread_id": thread_id,
        }
    return {
        "ok": True,
        "splice": splice,
        "dispatch": dispatched,
        "dispatch_thread_id": thread_id,
    }


async def _wait_sdk_closeout(
    thread_id: str,
    *,
    headers: dict[str, str] | None,
) -> dict[str, Any]:
    if headers is None:
        return {"ok": False, "error": "AGENT_BUS_TOKEN unset — cannot wait densify hop"}
    last: dict[str, Any] = {}
    async with make_async_client(
        DEFAULT_AGENT_BUS_URL, timeout=_WAIT_TIMEOUT + 5.0
    ) as bus:
        for _ in range(_WAIT_ROUNDS):
            try:
                resp = await bus.get(
                    f"/threads/{thread_id}/wait",
                    params={
                        "from_agent": "cursor-sdk",
                        "wait": int(_WAIT_TIMEOUT),
                        "completion": "proof_reply_from",
                    },
                    headers=headers,
                )
            except Exception as exc:
                return {"ok": False, "error": f"wait transport_error: {exc}"}
            try:
                last = resp.json() if resp.content else {}
            except Exception:
                last = {"raw": (resp.text or "")[:300]}
            if not isinstance(last, dict):
                last = {"value": last}
            if resp.status_code >= 400:
                last.setdefault("error", f"http_{resp.status_code}")
                return {
                    "ok": False,
                    "http_status": resp.status_code,
                    "error": str(last.get("error")),
                }
            status = str(last.get("status") or last.get("completion") or "")
            body = _closeout_body(last)
            if body and extract_densify_splice(body):
                return {"ok": True, "body": body}
            if status in {"complete", "completed", "error", "failed"}:
                if status in {"error", "failed"}:
                    return {
                        "ok": False,
                        "error": str(last.get("error") or status),
                        "body": body,
                    }
                return {"ok": True, "body": body}
    return {
        "ok": False,
        "error": "densify hop wait exhausted without a splice",
        "dispatch": last,
    }


def _closeout_body(payload: dict[str, Any]) -> str:
    turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
    for key in ("body", "text", "content"):
        value = payload.get(key) or turn.get(key)
        if isinstance(value, str) and value.strip():
            return value
    turns = payload.get("turns")
    if isinstance(turns, list) and turns:
        last = turns[-1]
        if isinstance(last, dict):
            for key in ("body", "text", "content"):
                value = last.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    return ""
