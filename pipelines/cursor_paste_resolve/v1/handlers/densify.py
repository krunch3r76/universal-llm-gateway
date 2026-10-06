"""Densify hop — optional cursor-sdk investigate before compose writes the paste file."""

from __future__ import annotations

import json
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
    parse_compose_options,
)
from .densify_wait import WAIT_CLIENT_TIMEOUT, latest_turn_number, wait_sdk_closeout
from .launch import agent_bus_headers, cursor_sdk_refuse_payload, post_json

_REQUEST_TIMEOUT = 30.0
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
                "failure_class": result.get("failure_class") or "hop_failed",
                "http_status": result.get("http_status"),
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
                    "failure_class": "empty_splice",
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
    headers = agent_bus_headers()
    if headers is None:
        payload = cursor_sdk_refuse_payload(
            reason="AGENT_BUS_TOKEN unset — cannot mint a densify thread",
            admit={"model": model, "job": "investigate"},
        )
        return {
            "ok": False,
            "failure_class": "token_unset",
            "http_status": 422,
            "error": payload["error"],
        }
    async with make_async_client(
        DEFAULT_AGENT_BUS_URL, timeout=WAIT_CLIENT_TIMEOUT
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
                "failure_class": "mint_failed",
                "http_status": mint.get("http_status") or 422,
                "error": f"create_thread failed: {mint['error']}",
                "dispatch": mint,
            }
        thread_id = str(mint.get("id") or "")
        if not thread_id or thread_id == MAESTRO_MEMO_THREAD:
            return {
                "ok": False,
                "failure_class": "mint_failed",
                "http_status": 422,
                "error": "create_thread returned no id"
                if not thread_id
                else (
                    "dispatch_thread_id 12286 is maestro memo only — "
                    "mint a work/review thread"
                ),
                "dispatch": mint,
            }
        after_turn = await latest_turn_number(bus, thread_id, headers)

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
                "failure_class": "dispatch_refused",
                "http_status": dispatched.get("http_status") or 422,
                "error": str(err)[:500],
                "dispatch": dispatched,
                "dispatch_thread_id": thread_id,
            }

        execution_id = str(dispatched.get("execution_id") or "")
        closeout = await wait_sdk_closeout(
            bus,
            thread_id,
            headers=headers,
            after_turn=after_turn,
            execution_id=execution_id,
        )
        if not closeout.get("ok"):
            return {
                "ok": False,
                "failure_class": closeout.get("failure_class") or "wait_failed",
                "http_status": closeout.get("http_status"),
                "error": closeout.get("error") or "densify wait failed",
                "dispatch": dispatched,
                "dispatch_thread_id": thread_id,
            }
        return {
            "ok": True,
            "splice": closeout.get("splice"),
            "dispatch": dispatched,
            "dispatch_thread_id": thread_id,
        }
