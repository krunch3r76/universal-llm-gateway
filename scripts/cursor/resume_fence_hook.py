#!/usr/bin/env python3
"""Cursor hook adapter for structural resume fence enforcement."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
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
_DENIAL_FALLBACK = _MARKER_DIR / "denied-fallback.jsonl"
_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_MCP_YAML = Path.home() / ".gateway/mcp.yaml"
_IDLE_S = int(os.environ.get("RESUME_FENCE_IDLE_S", "1800"))
_UNCOVERED_TOOLS = frozenset({"Grep", "Glob", "SearchConversations", "GetDynamicTools"})
_MCP_WRAPPER_TOOLS = frozenset({"CallDynamicTool", "call_mcp_tool", "Mcp", "mcp"})
# preToolUse reports MCP tools as ``MCP:<tool_name>`` (cursor.com/docs/hooks);
# beforeMCPExecution reports the bare name. read_set.mcp_allow rules are bare.
_MCP_TOOL_PREFIX = "MCP:"


class _HookDataError(Exception):
    """Malformed marker/read_set — deny with hook_error telemetry."""


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
        tool_input = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            tool_input = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    else:
        return {}

    args_raw = tool_input.get("arguments")
    inner: dict[str, Any] | None = None
    if isinstance(args_raw, str) and args_raw.strip():
        try:
            parsed = json.loads(args_raw)
            inner = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            inner = None
    elif isinstance(args_raw, dict):
        inner = args_raw

    if inner:
        for key, value in inner.items():
            if key not in tool_input or tool_input[key] in (None, ""):
                tool_input[key] = value
    return tool_input


def _coalesce_tool_payload(
    tool_input: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Merge MCP fields Cursor places on the payload root into tool_input (FIX-19)."""
    merged = dict(tool_input)
    for key in (
        "namespace",
        "toolName",
        "name",
        "tool",
        "arguments",
        "args",
        "input",
    ):
        val = payload.get(key)
        if val not in (None, "") and (
            key not in merged or merged.get(key) in (None, "")
        ):
            merged[key] = val
    nested = payload.get("input")
    if isinstance(nested, dict):
        for key, val in nested.items():
            if val not in (None, "") and (
                key not in merged or merged.get(key) in (None, "")
            ):
                merged[key] = val
    elif isinstance(nested, str) and nested.strip():
        try:
            parsed = json.loads(nested)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key, val in parsed.items():
                if val not in (None, "") and (
                    key not in merged or merged.get(key) in (None, "")
                ):
                    merged[key] = val
    return merged


def _scoped_thread_match(rule_thread: Any, tool_input: dict[str, Any]) -> bool:
    if rule_thread is None or rule_thread == "":
        return True
    actual = tool_input.get("thread")
    if actual is None or actual == "":
        return False
    return str(actual) == str(rule_thread)


def _mcp_call_allowed(
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    mcp_allow: list[dict[str, Any]],
) -> bool:
    inner_tool = str(tool_input.get("tool") or "")
    inner_op = str(tool_input.get("op") or "")
    op_candidates = {inner_op, inner_tool, tool_name}
    for rule in mcp_allow:
        rule_tool = str(rule.get("tool") or "")
        if rule_tool not in {tool_name, inner_tool}:
            continue
        ops = rule.get("ops") or []
        if ops and not any(str(op) in ops for op in op_candidates if op):
            continue
        if not _scoped_thread_match(rule.get("thread"), tool_input):
            continue
        paths = rule.get("paths")
        if paths:
            path = str(tool_input.get("path") or "")
            if isinstance(paths, list) and path not in paths:
                continue
        ids = rule.get("ids")
        id_prefix = rule.get("id_prefix")
        entity_id = str(
            tool_input.get("entity_id") or tool_input.get("id") or ""
        )
        if id_prefix:
            if not entity_id.startswith(str(id_prefix)):
                continue
        elif ids:
            if isinstance(ids, list) and entity_id not in ids:
                continue
        return True
    return False


def _first_hop_message(*, root: str, fence_id: str, transcript_id: str | None) -> str:
    tid_suffix = ""
    if transcript_id:
        tid_suffix = f", transcript_id={transcript_id}"
    # continuity is an overflow tool on user-vortex-code (canonical.yaml
    # surface_primary_domains.code omits it) — reachable only via dispatch.
    return (
        f"resume fence {fence_id}: call continuity(op=resume, thread={root}{tid_suffix}) "
        f'via user-vortex-code dispatch(tool="continuity", arguments=\'{{"op": "resume", '
        f'"thread": "{root}"}}\') — no GetDynamicTools / tool_search; '
        f"durable send must carry fence_id={fence_id}"
    )


def _deny(
    *,
    fence_id: str,
    root: str,
    agent_message: str,
    journal: dict[str, str],
) -> dict[str, Any]:
    return {
        "permission": "deny",
        "agent_message": agent_message,
        "journal": journal,
    }


def _readable(marker: dict[str, Any]) -> dict[str, Any]:
    read_set_raw = marker.get("read_set") or {}
    if not isinstance(read_set_raw, dict):
        raise _HookDataError("read_set is not a dict")
    readable = read_set_raw.get("readable") or {}
    if not isinstance(readable, dict):
        raise _HookDataError("read_set.readable is not a dict")
    return readable


def _mcp_allow_tool_names(mcp_allow: list[dict[str, Any]]) -> set[str]:
    return {str(rule.get("tool") or "") for rule in mcp_allow if rule.get("tool")}


def _decide_mcp(
    *,
    fence_id: str,
    root: str,
    transcript_id: str | None,
    tool_name: str,
    tool_input: dict[str, Any],
    readable: dict[str, Any],
) -> dict[str, Any]:
    mcp_allow = readable.get("mcp_allow") or []
    if _mcp_call_allowed(
        tool_name=tool_name,
        tool_input=tool_input,
        mcp_allow=mcp_allow,
    ):
        return {"permission": "allow"}
    return _deny(
        fence_id=fence_id,
        root=root,
        agent_message=_first_hop_message(
            root=root,
            fence_id=fence_id,
            transcript_id=transcript_id,
        ),
        journal={
            "surface": "mcp",
            "tool": tool_name,
            "op": str(tool_input.get("op") or tool_input.get("tool") or ""),
            "target": json.dumps(tool_input)[:500],
            "reason": "not_in_mcp_allow",
        },
    )


def _resolve_mcp_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    mcp_allow: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    if tool_name in _MCP_WRAPPER_TOOLS:
        return _unwrap_dynamic_tool(tool_name, tool_input)
    if tool_name in _mcp_allow_tool_names(mcp_allow):
        return tool_name, tool_input
    return _unwrap_dynamic_tool(tool_name, tool_input)


def _decide_mcp_hop(
    *,
    fence_id: str,
    root: str,
    transcript_id: str | None,
    tool_name: str,
    tool_input: dict[str, Any],
    payload: dict[str, Any],
    readable: dict[str, Any],
) -> dict[str, Any]:
    """Resolve MCP wrapper/direct tool names and enforce read_set."""
    mcp_allow = readable.get("mcp_allow") or []
    merged = _coalesce_tool_payload(tool_input, payload)
    bare_name = tool_name.removeprefix(_MCP_TOOL_PREFIX)
    mcp_name, mcp_input = _resolve_mcp_tool(bare_name, merged, mcp_allow=mcp_allow)
    # FIX-19b: wrapper shells may carry {} until execution time.
    if mcp_name in _MCP_WRAPPER_TOOLS:
        return {"permission": "allow"}
    verdict = _decide_mcp(
        fence_id=fence_id,
        root=root,
        transcript_id=transcript_id,
        tool_name=mcp_name,
        tool_input=mcp_input,
        readable=readable,
    )
    # FIX-19c: beforeMCPExecution may register tool_name=continuity with args
    # only at execution time (preToolUse already allowed the hop).
    if (
        verdict.get("permission") == "deny"
        and mcp_name == "continuity"
        and verdict.get("journal", {}).get("reason") == "not_in_mcp_allow"
        and not str(mcp_input.get("op") or "")
    ):
        return {"permission": "allow"}
    if verdict.get("permission") == "deny" and tool_name != bare_name:
        # Journal the wire name as observed so the firing event stays diagnosable.
        verdict["journal"]["tool"] = tool_name
    return verdict


def _decide_shell(
    *,
    fence_id: str,
    readable: dict[str, Any],
    command: str,
) -> dict[str, Any]:
    if readable.get("shell"):
        return {"permission": "allow"}
    return _deny(
        fence_id=fence_id,
        root="",
        agent_message=f"resume fence {fence_id}: shell denied during resume turn",
        journal={
            "surface": "shell",
            "tool": "shell",
            "op": "exec",
            "target": command[:500],
            "reason": "shell_false",
        },
    )


def _decide_read(
    *,
    fence_id: str,
    path: str,
    readable: dict[str, Any],
) -> dict[str, Any]:
    allowed_paths = readable.get("fs_paths") or []
    cortex_uris = readable.get("cortex_uris") or []
    if path in allowed_paths or any(uri in path for uri in cortex_uris):
        return {"permission": "allow"}
    return _deny(
        fence_id=fence_id,
        root="",
        agent_message=f"resume fence {fence_id}: file read denied — {path}",
        journal={
            "surface": "file",
            "tool": "read_file",
            "op": "read",
            "target": path,
            "reason": "path_not_in_read_set",
        },
    )


def _decide_uncovered_tool(
    *,
    fence_id: str,
    root: str,
    transcript_id: str | None,
    tool_name: str,
    tool_input: dict[str, Any],
) -> dict[str, Any]:
    return _deny(
        fence_id=fence_id,
        root=root,
        agent_message=_first_hop_message(
            root=root,
            fence_id=fence_id,
            transcript_id=transcript_id,
        ),
        journal={
            "surface": "tool",
            "tool": tool_name,
            "op": str(tool_input.get("op") or ""),
            "target": json.dumps(tool_input)[:500],
            "reason": "tool_not_in_read_set",
        },
    )


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
    transcript_id = str(marker.get("transcript_id") or "")

    try:
        readable = _readable(marker)

        if event == "beforeMCPExecution":
            tool_name = str(payload.get("tool_name") or "")
            tool_input = _parse_tool_input(
                payload.get("tool_input") or payload.get("arguments")
            )
            return _decide_mcp_hop(
                fence_id=fence_id,
                root=root,
                transcript_id=transcript_id or None,
                tool_name=tool_name,
                tool_input=tool_input,
                payload=payload,
                readable=readable,
            )

        if event == "beforeShellExecution":
            command = str(payload.get("command") or "")
            return _decide_shell(
                fence_id=fence_id,
                readable=readable,
                command=command,
            )

        if event == "beforeReadFile":
            path = str(payload.get("file_path") or payload.get("path") or "")
            return _decide_read(fence_id=fence_id, path=path, readable=readable)

        if event == "preToolUse":
            tool_name = str(payload.get("tool_name") or "")
            tool_input = _parse_tool_input(
                payload.get("tool_input") or payload.get("arguments")
            )
            if tool_name in _UNCOVERED_TOOLS:
                return _decide_uncovered_tool(
                    fence_id=fence_id,
                    root=root,
                    transcript_id=transcript_id or None,
                    tool_name=tool_name,
                    tool_input=tool_input,
                )
            if tool_name in {"Shell", "run_terminal_cmd"}:
                command = str(
                    tool_input.get("command")
                    or payload.get("command")
                    or ""
                )
                return _decide_shell(
                    fence_id=fence_id,
                    readable=readable,
                    command=command,
                )
            if tool_name in {"Read", "read_file"}:
                path = str(
                    tool_input.get("path")
                    or tool_input.get("file_path")
                    or payload.get("file_path")
                    or ""
                )
                return _decide_read(fence_id=fence_id, path=path, readable=readable)
            if tool_name in _MCP_WRAPPER_TOOLS or payload.get("tool_input"):
                return _decide_mcp_hop(
                    fence_id=fence_id,
                    root=root,
                    transcript_id=transcript_id or None,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    payload=payload,
                    readable=readable,
                )
            return {"permission": "allow"}

        if event == "sessionStart":
            return {}

    except _HookDataError as exc:
        return _deny(
            fence_id=fence_id,
            root=root,
            agent_message=f"resume fence {fence_id}: hook data error — {exc}",
            journal={
                "surface": "hook",
                "tool": event,
                "op": "error",
                "target": str(exc)[:500],
                "reason": "hook_error",
            },
        )
    except Exception:
        return _deny(
            fence_id=fence_id,
            root=root,
            agent_message=f"resume fence {fence_id}: fail-closed on hook error",
            journal={
                "surface": "hook",
                "tool": event,
                "op": "error",
                "target": "",
                "reason": "fail_closed",
            },
        )

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


def _maybe_expire_local(fold: dict[str, Any] | None, marker: dict[str, Any]) -> dict[str, Any] | None:
    if not fold:
        return fold
    state = str(fold.get("state") or "")
    if state in {"released", "expired"}:
        return fold
    last_at_raw = fold.get("last_event_at")
    if not last_at_raw:
        return fold
    try:
        last_at = datetime.fromisoformat(str(last_at_raw).replace("Z", "+00:00"))
    except ValueError:
        return fold
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=UTC)
    idle_s = int((datetime.now(UTC) - last_at).total_seconds())
    if idle_s <= _IDLE_S:
        return fold
    fence_id = str(marker.get("fence_id") or fold.get("fence_id") or "")
    if not fence_id:
        return fold
    try:
        with _client() as client:
            client.post(
                f"/resume-fences/{fence_id}/expire",
                json={"idle_seconds": idle_s},
            )
    except httpx.HTTPError:
        return fold
    return _fetch_fold(fence_id)


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
    row = {"fence_id": fence_id, **journal, "ts": datetime.now(UTC).isoformat()}
    try:
        with _client() as client:
            client.post(f"/resume-fences/{fence_id}/denied", json=journal)
        return
    except httpx.HTTPError:
        pass
    _DENIAL_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    with _DENIAL_FALLBACK.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _arm_fence(
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
            resp = client.post(f"/threads/{thread}/resume-fence/arm", json=body)
        if resp.status_code >= 400:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


def _unwrap_dynamic_tool(
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if tool_name not in _MCP_WRAPPER_TOOLS:
        return tool_name, tool_input
    inner_name = str(
        tool_input.get("toolName")
        or tool_input.get("tool")
        or tool_input.get("name")
        or tool_input.get("tool_name")
        or tool_name
    )
    inner_raw = (
        tool_input.get("arguments")
        or tool_input.get("args")
        or tool_input.get("input")
        or {}
    )
    if isinstance(inner_raw, str) and inner_raw.strip():
        try:
            inner_raw = json.loads(inner_raw)
        except json.JSONDecodeError:
            inner_raw = {}
    if not isinstance(inner_raw, dict):
        inner_raw = {}
    merged = _parse_tool_input({"tool": inner_name, "arguments": inner_raw})
    return inner_name, merged


def handle_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one hook event."""
    conversation_id = str(payload.get("conversation_id") or "")

    if event == "beforeSubmitPrompt":
        prompt = str(payload.get("prompt") or "")
        match = _RESUME_RE.match(prompt)
        if match and conversation_id:
            thread = match.group(1)
            if _load_marker(conversation_id) is None:
                bundle = _arm_fence(
                    thread,
                    transcript_id=conversation_id,
                    source="hook_prompt",
                )
                if bundle and bundle.get("fence"):
                    fence = bundle["fence"]
                    carriage = bundle.get("fence_carriage") or {}
                    marker_data: dict[str, Any] = {
                        "fence_id": fence.get("fence_id"),
                        "root": thread,
                        "transcript_id": conversation_id,
                        "state": fence.get("state", "armed"),
                        "source": "agent_bus",
                        "fetched_at": fence.get("opened_at"),
                        "read_set": bundle.get("read_set"),
                        "first_hop": carriage.get("first_hop"),
                    }
                    preview = bundle.get("mission_preview")
                    if isinstance(preview, dict):
                        marker_data["mission_preview"] = preview
                    _save_marker(conversation_id, marker_data)
        return {"continue": True}

    marker = _load_marker(conversation_id) if conversation_id else None
    fold = None
    if marker and marker.get("fence_id"):
        fold = _fetch_fold(str(marker["fence_id"]))
        fold = _maybe_expire_local(fold, marker)

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
