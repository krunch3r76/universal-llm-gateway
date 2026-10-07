"""Launch step — Glass/IDE script argv or cursor_sdk team_dispatch admit."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_STARGATE_URL,
    make_async_client,
)

from ._message import (
    ALLOWED_HOSTS,
    LAUNCH_TARGETS,
    MAESTRO_MEMO_THREAD,
    cursor_sdk_dispatch_body,
    parse_compose_options,
    tab_model_query,
    team_dispatch_admit_shape,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30.0
_REPO = Path(__file__).resolve().parents[4]
BridgeRunner = Callable[[list[str], dict[str, str]], dict[str, Any]]


def _step(payload: dict[str, Any], *, error: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload, default=str), json=payload, error=error)


def bridge_remote_env(window: str, model_query: str = "") -> str:
    """Remote export string for open-tab. Empty model_query skips Ctrl+/."""
    parts = [
        "WAYLAND_DISPLAY=wayland-1",
        "XDG_RUNTIME_DIR=/run/user/1000",
        "CURSOR_BRIDGE_UINPUT_ENABLED=1",
        f"CURSOR_BRIDGE_WINDOW={window}",
    ]
    query = (model_query or "").strip()
    if query and all(ch.isalnum() or ch in "._-" for ch in query):
        parts.append(f"CURSOR_BRIDGE_MODEL_QUERY={query}")
    return " ".join(parts)


def bridge_argv(repo: Path, message_file: Path, thread: str) -> list[str]:
    python = str(Path.home() / ".venvs/universal/bin/python")
    return [
        python,
        str(repo / "scripts" / "cursor-bridge-launch.py"),
        "open-tab",
        "--force",
        "--thread",
        thread,
        "--slug",
        "cursor-paste",
        "--message-file",
        str(message_file),
    ]


def paste_thread_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%H%M%S")
    return f"paste-{stamp}"


def cursor_sdk_refuse_payload(
    *,
    reason: str,
    admit: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": False,
        "http_status": 422,
        "error": reason,
        "admit": admit,
    }


def default_bridge_runner(argv: list[str], env: dict[str, str]) -> dict[str, Any]:
    proc = subprocess.run(
        argv,
        env=env,
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    parsed: dict[str, Any] | None = None
    if stdout:
        try:
            loaded = json.loads(stdout)
            if isinstance(loaded, dict):
                parsed = loaded
        except json.JSONDecodeError:
            parsed = None
    return {
        "returncode": proc.returncode,
        "stdout": stdout[-4000:],
        "stderr": (proc.stderr or "")[-2000:],
        "parsed": parsed,
    }


class CursorPasteLaunchHandler(BaseHandler):
    step_type = "cursor_paste_resolve_launch_v1"
    bridge_runner: BridgeRunner = staticmethod(default_bridge_runner)
    repo: Path = _REPO

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        bound = parse_compose_options(opts)
        if isinstance(bound, str):
            return _step({"ok": False, "error": bound}, error=bound)

        composed = _compose_json(context)
        if composed.get("ok") is not True:
            err = "compose did not succeed — do not launch"
            detail = composed.get("error")
            if detail:
                err = f"{err}: {detail}"
            return _step(
                {"ok": False, "error": err, "compose": composed or None},
                error=err,
            )
        message_path = str(composed.get("message_path") or "")
        target = bound["launch_target"]
        if not target:
            return _step(
                {
                    "ok": True,
                    "skipped": True,
                    "reason": "launch_target empty — compose only",
                    "message_path": message_path,
                }
            )
        if target not in LAUNCH_TARGETS:
            err = f"unknown launch_target {target!r}"
            return _step(
                {
                    "ok": False,
                    "http_status": 422,
                    "error": err,
                    "allowed": sorted(LAUNCH_TARGETS),
                },
                error=err,
            )
        if not message_path or not Path(message_path).is_file():
            err = "message-file missing before paste"
            return _step(
                {"ok": False, "error": err, "message_path": message_path},
                error=err,
            )

        if target in {"glass", "ide"}:
            return await _launch_bridge(self, bound, Path(message_path), window=target)
        return await _launch_cursor_sdk(bound, Path(message_path))


def _compose_json(context: Any) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    compose = outputs.get("compose")
    json_out = getattr(compose, "json", None) if compose is not None else None
    return json_out if isinstance(json_out, dict) else {}


async def _launch_bridge(
    handler: CursorPasteLaunchHandler,
    bound: dict[str, Any],
    message_file: Path,
    *,
    window: str,
) -> StepOutput:
    host = bound["host"]
    if host not in ALLOWED_HOSTS:
        err = "host omitted or not orion-node|jupiter — name the two hosts"
        return _step(
            {"ok": False, "error": err, "allowed_hosts": sorted(ALLOWED_HOSTS)},
            error=err,
        )
    thread = paste_thread_name()
    argv = bridge_argv(handler.repo, message_file, thread)
    env = os.environ.copy()
    env["CURSOR_BRIDGE_SSH_HOST"] = host
    env["CURSOR_BRIDGE_WINDOW"] = window
    query = tab_model_query(bound.get("tab_model") or "")
    env["CURSOR_BRIDGE_REMOTE_ENV"] = bridge_remote_env(window, query)
    if query:
        env["CURSOR_BRIDGE_MODEL_QUERY"] = query
    result = await asyncio.to_thread(handler.bridge_runner, argv, env)
    parsed = result.get("parsed") if isinstance(result, dict) else None
    payload: dict[str, Any] = {
        "ok": bool(parsed.get("ok"))
        if isinstance(parsed, dict)
        else result.get("returncode") == 0,
        "launch_target": window,
        "host": host,
        "argv": argv,
        "thread": thread,
        "keystroke": (parsed or {}).get("keystroke")
        if isinstance(parsed, dict)
        else None,
        "focused": (parsed or {}).get("focused") if isinstance(parsed, dict) else None,
        "script": result,
    }
    err = None if payload["ok"] else "cursor-bridge-launch.py open-tab failed"
    return _step(payload, error=err)


async def _launch_cursor_sdk(bound: dict[str, Any], message_file: Path) -> StepOutput:
    kind = bound["kind"]
    assertion_id = bound["assertion_id"]
    thread_id = bound["dispatch_thread_id"]
    admit = team_dispatch_admit_shape(
        kind=kind,
        assertion_id=assertion_id,
        message_path=str(message_file),
        dispatch_thread_id=thread_id or "<minted>",
        reuse_thread=thread_id or "",
    )
    if thread_id == MAESTRO_MEMO_THREAD:
        payload = cursor_sdk_refuse_payload(
            reason="dispatch_thread_id 12286 is maestro memo only — mint a work/review thread",
            admit=admit,
        )
        return _step(payload, error=payload["error"])

    prompt = message_file.read_text(encoding="utf-8")
    if not thread_id:
        headers = agent_bus_headers()
        if headers is None:
            payload = cursor_sdk_refuse_payload(
                reason="AGENT_BUS_TOKEN unset — cannot mint a thread",
                admit=admit,
            )
            return _step(payload, error=payload["error"])
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL, timeout=_REQUEST_TIMEOUT
        ) as bus:
            slug = f"cursor-paste-{kind}-{assertion_id}"
            mint = await _post_json(
                bus,
                "/threads",
                {"slug": slug, "idempotency_key": slug},
                headers=headers,
            )
            if "error" in mint:
                payload = cursor_sdk_refuse_payload(
                    reason=f"create_thread failed: {mint['error']}",
                    admit=admit,
                )
                return _step(payload, error=payload["error"])
            thread_id = str(mint.get("id") or "")
            if not thread_id:
                payload = cursor_sdk_refuse_payload(
                    reason="create_thread returned no id",
                    admit=admit,
                )
                return _step(payload, error=payload["error"])
            admit["dispatch_thread_id"] = thread_id
            admit["reuse_thread"] = thread_id

    body = cursor_sdk_dispatch_body(
        kind=kind,
        assertion_id=assertion_id,
        prompt=prompt,
        dispatch_thread_id=thread_id,
        reuse_thread=thread_id,
    )
    async with make_async_client(
        DEFAULT_STARGATE_URL, timeout=_REQUEST_TIMEOUT
    ) as stargate:
        dispatched = await _post_json(stargate, "/api/v1/team/dispatch", body)
    if dispatched.get("error") or (
        isinstance(dispatched.get("status_code"), int)
        and dispatched["status_code"] >= 400
    ):
        err = dispatched.get("error") or dispatched
        payload = {
            "ok": False,
            "http_status": dispatched.get("http_status") or 422,
            "error": str(err)[:500],
            "admit": admit,
            "dispatch": dispatched,
        }
        return _step(payload, error=payload["error"])
    return _step(
        {
            "ok": True,
            "launch_target": "cursor_sdk",
            "dispatch_thread_id": thread_id,
            "admit": admit,
            "dispatch": dispatched,
        }
    )


def agent_bus_headers() -> dict[str, str] | None:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


async def _post_json(
    client: Any,
    path: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        resp = await client.post(path, json=body, headers=headers)
    except Exception as exc:
        return {"error": f"transport_error: {exc}"}
    try:
        data = resp.json()
    except Exception:
        data = {"raw": (resp.text or "")[:300]}
    if not isinstance(data, dict):
        data = {"value": data}
    if resp.status_code >= 400:
        data.setdefault("error", f"http_{resp.status_code}")
        data["http_status"] = resp.status_code
        data["status_code"] = resp.status_code
    return data


post_json = _post_json
