#!/usr/bin/env python3
"""Cursor hook adapter for structural resume fence enforcement."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

_RESUME_RE = re.compile(r"^\s*resume\s+(\d{3,6})\b", re.IGNORECASE)
_MARKER_DIR = Path(
    os.environ.get(
        "RESUME_FENCE_MARKER_DIR",
        str(Path.home() / ".agent-bus/resume-fence"),
    )
)
_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_MCP_YAML = Path.home() / ".gateway/mcp.yaml"


def _agent_bus_token() -> str:
    if not _MCP_YAML.is_file():
        return ""
    try:
        import yaml

        cfg = yaml.safe_load(_MCP_YAML.read_text()) or {}
    except Exception:
        return ""
    return str(cfg.get("AGENT_BUS_TOKEN") or "").strip()


def _client() -> httpx.Client:
    headers = {}
    token = _agent_bus_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_SOCK),
        base_url="http://agent-bus",
        headers=headers,
        timeout=10.0,
    )


def _parse_tool_input(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _mcp_call_allowed(
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    mcp_allow: list[dict[str, Any]],
) -> bool:
    inner_tool = str(tool_input.get("tool") or tool_name)
    inner_op = str(tool_input.get("op") or tool_input.get("tool") or "")
    for rule in mcp_allow:
        if str(rule.get("tool")) != inner_tool and str(rule.get("tool")) != tool_name:
            continue
        ops = rule.get("ops") or []
        if ops and inner_op and inner_op not in ops:
            continue
        rule_thread = rule.get("thread")
        if rule_thread and str(tool_input.get("thread") or "") not in {
            str(rule_thread),
            tool_input_num(tool_input.get("thread")),
        }:
            continue
        paths = rule.get("paths")
        if paths:
            path = str(tool_input.get("path") or "")
            if isinstance(paths, list) and path not in paths:
                continue
        ids = rule.get("ids")
        if ids:
            entity_id = str(
                tool_input.get("entity_id")
                or tool_input.get("id")
                or tool_input.get("arguments", {}).get("entity_id", "")
            )
            if isinstance(ids, list) and entity_id not in ids:
                continue
        return True
    return False


def tool_input_num(value: Any) -> str:
    return str(value) if value is not None else ""


def decide(
    *,
    event: str,
    payload: dict[str, Any],
    marker: dict[str, Any] | None,
    fold: dict[str, Any] | None,
) -> dict[str, Any]:
    """Pure verdict function for unit tests."""
    if event == "beforeSubmitPrompt":
        return {"continue": True}

    if marker is None:
        return {"permission": "allow"}

    state = str((fold or {}).get("state") or marker.get("state") or "")
    if state in {"released", "expired"}:
        return {"permission": "allow", "delete_marker": True}

    fence_id = str(marker.get("fence_id") or "")
    root = str(marker.get("root") or marker.get("root_thread") or "")

    try:
        read_set_raw = marker.get("read_set") or {}
        read_set = read_set_raw if isinstance(read_set_raw, dict) else {}
        readable = read_set.get("readable") or {}
        mcp_allow = readable.get("mcp_allow") or []
        if event == "beforeMCPExecution":
            tool_name = str(payload.get("tool_name") or "")
            tool_input = _parse_tool_input(payload.get("tool_input"))
            if _mcp_call_allowed(
                tool_name=tool_name,
                tool_input=tool_input,
                mcp_allow=mcp_allow,
            ):
                return {"permission": "allow"}
            return {
                "permission": "deny",
                "agent_message": (
                    f"resume fence {fence_id}: call continuity(op=resume, thread={root}) "
                    "and cite only bundle ids; this call was denied and journaled"
                ),
                "journal": {
                    "surface": "mcp",
                    "tool": tool_name,
                    "op": str(tool_input.get("op") or tool_input.get("tool") or ""),
                    "target": json.dumps(tool_input)[:500],
                    "reason": "not_in_mcp_allow",
                },
            }

        if event == "beforeShellExecution":
            if readable.get("shell"):
                return {"permission": "allow"}
            return {
                "permission": "deny",
                "agent_message": (
                    f"resume fence {fence_id}: shell denied during resume turn"
                ),
                "journal": {
                    "surface": "shell",
                    "tool": "shell",
                    "op": "exec",
                    "target": str(payload.get("command") or "")[:500],
                    "reason": "shell_false",
                },
            }

        if event == "beforeReadFile":
            path = str(payload.get("file_path") or payload.get("path") or "")
            allowed_paths = readable.get("fs_paths") or []
            cortex_uris = readable.get("cortex_uris") or []
            if path in allowed_paths or any(uri in path for uri in cortex_uris):
                return {"permission": "allow"}
            return {
                "permission": "deny",
                "agent_message": (
                    f"resume fence {fence_id}: file read denied — {path}"
                ),
                "journal": {
                    "surface": "file",
                    "tool": "read_file",
                    "op": "read",
                    "target": path,
                    "reason": "path_not_in_read_set",
                },
            }

        if event == "sessionStart":
            return {}

    except Exception:
        return {
            "permission": "deny",
            "agent_message": f"resume fence {fence_id}: fail-closed on hook error",
            "journal": {
                "surface": "hook",
                "tool": event,
                "op": "error",
                "target": "",
                "reason": "fail_closed",
            },
        }

    return {"permission": "allow"}


def _marker_path(conversation_id: str) -> Path:
    _MARKER_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", conversation_id)
    return _MARKER_DIR / f"{safe}.json"


def _load_marker(conversation_id: str) -> dict[str, Any] | None:
    path = _marker_path(conversation_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _save_marker(conversation_id: str, data: dict[str, Any]) -> None:
    _marker_path(conversation_id).write_text(json.dumps(data, indent=2))


def _delete_marker(conversation_id: str) -> None:
    path = _marker_path(conversation_id)
    if path.is_file():
        path.unlink()


def _fetch_fold(fence_id: str) -> dict[str, Any] | None:
    try:
        with _client() as client:
            resp = client.get(f"/resume-fences/{fence_id}")
        if resp.status_code >= 400:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


def _journal_denied(fence_id: str, journal: dict[str, str]) -> None:
    try:
        with _client() as client:
            client.post(f"/resume-fences/{fence_id}/denied", json=journal)
    except httpx.HTTPError:
        pass


def _pour_fence(
    thread: str,
    *,
    transcript_id: str | None,
    source: str,
) -> dict[str, Any] | None:
    body: dict[str, Any] = {"source": source}
    if transcript_id:
        body["transcript_id"] = transcript_id
    try:
        with _client() as client:
            resp = client.post(f"/threads/{thread}/resume-fence", json=body)
        if resp.status_code >= 400:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


def handle_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one hook event."""
    conversation_id = str(payload.get("conversation_id") or "")

    if event == "beforeSubmitPrompt":
        prompt = str(payload.get("prompt") or "")
        match = _RESUME_RE.match(prompt)
        if match and conversation_id:
            thread = match.group(1)
            if _load_marker(conversation_id) is None:
                bundle = _pour_fence(
                    thread,
                    transcript_id=conversation_id,
                    source="hook_prompt",
                )
                if bundle and bundle.get("fence"):
                    fence = bundle["fence"]
                    _save_marker(
                        conversation_id,
                        {
                            "fence_id": fence.get("fence_id"),
                            "root": thread,
                            "state": fence.get("state", "poured"),
                            "source": "agent_bus",
                            "fetched_at": fence.get("opened_at"),
                            "read_set": bundle.get("read_set"),
                        },
                    )
        return {"continue": True}

    marker = _load_marker(conversation_id) if conversation_id else None
    fold = None
    if marker and marker.get("fence_id"):
        fold = _fetch_fold(str(marker["fence_id"]))

    verdict = decide(event=event, payload=payload, marker=marker, fold=fold)
    if verdict.get("delete_marker") and conversation_id:
        _delete_marker(conversation_id)
        verdict = {"permission": "allow"}

    if verdict.get("permission") == "deny" and marker:
        journal = verdict.pop("journal", None)
        if journal and marker.get("fence_id"):
            _journal_denied(str(marker["fence_id"]), journal)

    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume fence Cursor hook")
    parser.add_argument("--event", required=True)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    result = handle_event(args.event, payload)
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
