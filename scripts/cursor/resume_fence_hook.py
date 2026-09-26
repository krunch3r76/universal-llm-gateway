#!/usr/bin/env python3
"""Cursor hook adapter for structural resume fence first-hop nudge.

The house is undenied: this hook never returns ``permission=deny``.
While a tab is ``armed``, bleed-class discovery tools get an
``agent_message`` pointing at ``continuity(op=resume)``. Pour (``tape``),
cortex, rename, and every other call stay allowed. ``sessionStart``
drops the local marker so a Cursor restart actually unfences the tab.
"""

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
_BLEED_TOOLS = frozenset({"Grep", "Glob", "SearchConversations", "GetDynamicTools"})


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


def _first_hop_message(*, root: str, fence_id: str, transcript_id: str | None) -> str:
    tid_suffix = ""
    if transcript_id:
        tid_suffix = f", transcript_id={transcript_id}"
    return (
        f"resume fence {fence_id}: first hop is continuity(op=resume, "
        f"thread={root}{tid_suffix}) via user-vortex-code "
        f'dispatch(tool="continuity", arguments=\'{{"op": "resume", '
        f'"thread": "{root}"}}\') — pour is tape; this hook does not deny'
    )


def _nudge(
    *,
    fence_id: str,
    root: str,
    agent_message: str,
    journal: dict[str, str],
) -> dict[str, Any]:
    return {
        "permission": "allow",
        "agent_message": agent_message,
        "journal": journal,
    }


def decide(
    *,
    event: str,
    payload: dict[str, Any],
    marker: dict[str, Any] | None,
    fold: dict[str, Any] | None,
) -> dict[str, Any]:
    """Pure verdict: allow always; nudge bleed tools only while armed."""
    if event == "beforeSubmitPrompt":
        return {"continue": True}
    if event == "sessionStart":
        return {"delete_marker": True}

    if marker is None:
        return {"permission": "allow"}

    try:
        state = str((fold or {}).get("state") or marker.get("state") or "")
        if state in {"released", "expired"}:
            return {"permission": "allow", "delete_marker": True}

        if state == "armed" and event in {"preToolUse", "beforeMCPExecution"}:
            tool_name = str(payload.get("tool_name") or "")
            if tool_name in _BLEED_TOOLS:
                fence_id = str(marker.get("fence_id") or "")
                root = str(marker.get("root") or marker.get("root_thread") or "")
                transcript_id = str(marker.get("transcript_id") or "") or None
                tool_input = payload.get("tool_input")
                if not isinstance(tool_input, dict):
                    tool_input = {}
                return _nudge(
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
                        "reason": "bleed_nudge",
                    },
                )
        return {"permission": "allow"}
    except Exception:
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
    body: dict[str, Any] = {"source": source, "surface": "cursor"}
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


def handle_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Route one Cursor hook event, persist/clear the tab marker, and journal nudges."""
    conversation_id = str(payload.get("conversation_id") or "")

    if event == "sessionStart":
        if conversation_id:
            _delete_marker(conversation_id)
        return {}

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

    journal = verdict.pop("journal", None)
    if journal and marker and marker.get("fence_id"):
        _journal_denied(str(marker["fence_id"]), journal)

    return verdict


def main() -> int:
    """Read one hook payload from stdin and emit the JSON verdict on stdout."""
    parser = argparse.ArgumentParser(description="Resume fence Cursor hook")
    parser.add_argument("--event", required=True)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    result = handle_event(args.event, payload)
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
