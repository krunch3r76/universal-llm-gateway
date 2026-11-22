#!/usr/bin/env python3
"""preToolUse / subagentStart hook: deny native Task helpers outside policy."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_DENY_MESSAGE = (
    "Native Cursor Task subagents are denied on cursor-sdk dispatches. "
    "Use bounded in-seat Grep/Read/Glob or team_dispatch for breadth work."
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_policy() -> dict[str, Any]:
    policy_path = Path.home() / ".cursor" / "native_helper_policy.json"
    if not policy_path.is_file():
        return {"allow_kinds": [], "enforcement": "unenforced"}
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"allow_kinds": [], "enforcement": "unenforced"}
    return data if isinstance(data, dict) else {}


def _subagent_kind(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = payload.get("args")
    if not isinstance(tool_input, dict):
        return "unspecified"
    for key in ("subagentType", "subagent_type", "subagent_type"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        if isinstance(value, dict):
            kind = value.get("kind")
            if isinstance(kind, str) and kind.strip():
                return kind.strip().lower()
    return "unspecified"


def _tool_name(payload: dict[str, Any]) -> str:
    for key in ("tool_name", "toolName", "tool"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_team_dispatch_mcp(payload: dict[str, Any]) -> bool:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    tool = str(tool_input.get("tool") or tool_input.get("name") or "").lower()
    if "team_dispatch" in tool:
        return True
    arguments = tool_input.get("arguments")
    if isinstance(arguments, str) and "team_dispatch" in arguments:
        return True
    return False


def decide(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = _tool_name(payload)
    if tool_name and tool_name.lower() not in {"task"}:
        return {"permission": "allow"}
    if _is_team_dispatch_mcp(payload):
        return {"permission": "allow"}
    policy = _load_policy()
    if policy.get("enforcement") != "enforced":
        return {"permission": "allow"}
    allow = {
        str(k).strip().lower()
        for k in (policy.get("allow_kinds") or [])
        if isinstance(k, str) and k.strip()
    }
    kind = _subagent_kind(payload)
    if kind in allow:
        return {"permission": "allow"}
    dispatch_id = os.environ.get("CURSOR_SDK_DISPATCH_ID", "").strip() or "unknown"
    record = {
        "kind": kind,
        "tool_name": tool_name or "Task",
        "denied": True,
        "dispatch_id": dispatch_id,
    }
    try:
        if str(_repo_root()) not in sys.path:
            sys.path.insert(0, str(_repo_root()))
        from services.git_integration_worker.cursor_sdk_native_helper_policy import (
            append_native_helper_attempt,
        )

        if dispatch_id != "unknown":
            append_native_helper_attempt(dispatch_id, record)
    except Exception:
        pass
    return {
        "permission": "deny",
        "agent_message": _DENY_MESSAGE,
        "user_message": _DENY_MESSAGE,
    }


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        response = decide(payload)
        sys.stdout.write(json.dumps(response))
    except Exception:
        sys.stdout.write(json.dumps({"permission": "deny", "agent_message": _DENY_MESSAGE}))


if __name__ == "__main__":
    main()
