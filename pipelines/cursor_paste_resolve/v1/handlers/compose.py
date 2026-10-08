"""Compose step — assertion_get + implementer read + message-file write."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, override

from implement_admission.closeout_helpers import cortex_files_root, workspaces_root
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_CORTEX_URL, make_async_client

from ._cortex import cortex_dispatch
from ._message import (
    IMPLEMENTER_REL,
    IMPLEMENTER_URI,
    compose_message,
    parse_compose_options,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 15.0


def _step(payload: dict[str, Any], *, error: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload, default=str), json=payload, error=error)


def read_implementer(files_root: Path | None = None) -> str | None:
    root = files_root if files_root is not None else cortex_files_root()
    path = (root / IMPLEMENTER_REL).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


class CursorPasteComposeHandler(BaseHandler):
    step_type = "cursor_paste_resolve_compose_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        bound = parse_compose_options(opts)
        if isinstance(bound, str):
            return _step({"ok": False, "error": bound}, error=bound)

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

        investigate_out = _investigate_json(context)
        if bound["investigate"]:
            if (
                investigate_out.get("ok") is not True
                or not str(investigate_out.get("splice") or "").strip()
            ):
                err = str(
                    investigate_out.get("error")
                    or "investigate hop did not produce a splice"
                )
                return _step(
                    {
                        "ok": False,
                        "error": err,
                        "investigate": investigate_out or None,
                    },
                    error=err,
                )

        implementer = read_implementer()
        if implementer is None:
            err = f"implementer URI missing: {IMPLEMENTER_URI}"
            return _step({"ok": False, "error": err}, error=err)

        rel = f"tmp/prompts/cursor-paste-{bound['kind']}-{bound['assertion_id']}.md"
        dest = workspaces_root() / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        investigate_model = str(investigate_out.get("model") or "")
        investigate_splice = str(investigate_out.get("splice") or "")
        body = compose_message(
            bound["kind"],
            bound["assertion_id"],
            bound["notify"],
            implementer,
            investigate_model=investigate_model,
            investigate_splice=investigate_splice,
            tab_model=bound["tab_model"],
            operator_note=bound["operator_note"],
            finalize=bound["finalize"],
        )
        dest.write_text(body, encoding="utf-8")
        payload = {
            "ok": True,
            "kind": bound["kind"],
            "assertion_id": bound["assertion_id"],
            "window": bound["window"],
            "host": bound["host"],
            "notify": bound["notify"],
            "launch_target": bound["launch_target"],
            "investigate": bound["investigate"],
            "tab_model": bound["tab_model"],
            "operator_note": bound["operator_note"],
            "finalize": bound["finalize"],
            "message_path": str(dest),
            "implementer_uri": IMPLEMENTER_URI,
        }
        logger.info("cursor_paste_resolve compose wrote %s", dest)
        return _step(payload)


def _investigate_json(context: Any) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    investigate = outputs.get("investigate")
    json_out = getattr(investigate, "json", None) if investigate is not None else None
    return json_out if isinstance(json_out, dict) else {}
