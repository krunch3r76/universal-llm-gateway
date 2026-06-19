"""Transcript reconstruction from cursor_sdk run.conversation() — closeout sidecar body."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping


def _tool_call_section(message: Mapping) -> str | None:
    """Render a ``toolCall`` step (raw cursor_sdk message dict) as transcript text.

    cursor_sdk leaves the toolCall ``message`` unparsed (types.py:1961-1962), so we
    read wire keys defensively: ``type`` (tool kind), ``args`` (inputs, incl. shell
    ``command``), and ``result`` (output). For shell tools, stdout/stderr/exitCode
    live under ``result.value`` on success or ``result.error`` on failure.
    """
    tool = str(message.get("type") or "tool")
    args = message.get("args") if isinstance(message.get("args"), Mapping) else {}
    command = args.get("command") if isinstance(args, Mapping) else None
    if isinstance(command, str) and command.strip():
        header = f"$ [{tool}] {command.strip()}"
    elif isinstance(args, Mapping) and args:
        header = f"$ [{tool}] {json.dumps(args, separators=(',', ':'))[:500]}"
    else:
        header = f"$ [{tool}]"

    output = _tool_result_text(message.get("result"))
    return f"{header}\n{output}" if output else header


def _tool_result_text(result: object) -> str | None:
    """Best-effort extraction of tool output (stdout/stderr/error) from a result dict."""
    if not isinstance(result, Mapping):
        return None
    if result.get("status") == "error":
        return f"[error] {result.get('error')}"
    value = result.get("value")
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key in ("stdout", "stderr"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                parts.append(f"[{key}]\n{text.rstrip()}")
        exit_code = value.get("exitCode")
        if exit_code is not None:
            parts.append(f"[exitCode] {exit_code}")
        if parts:
            return "\n".join(parts)
        return json.dumps(value, separators=(",", ":"))[:2000]
    if value is not None:
        return str(value)[:2000]
    return None


def _conversation_step_section(step: object) -> str | None:
    """Render one agent-turn conversation step, or None to skip (thinking/unknown)."""
    step_type = getattr(step, "type", None)
    message = getattr(step, "message", None)
    if step_type == "assistantMessage":
        text = getattr(message, "text", None)
        return text if isinstance(text, str) and text.strip() else None
    if step_type == "toolCall" and isinstance(message, Mapping):
        return _tool_call_section(message)
    return None


def _shell_turn_section(inner: object) -> str | None:
    """Render a shellConversationTurn (stdout/stderr exposed on the turn itself)."""
    shell_cmd = getattr(inner, "shell_command", None)
    shell_out = getattr(inner, "shell_output", None)
    bits: list[str] = []
    command = getattr(shell_cmd, "command", None)
    if isinstance(command, str) and command.strip():
        bits.append(f"$ {command.strip()}")
    for attr in ("stdout", "stderr"):
        text = getattr(shell_out, attr, None)
        if isinstance(text, str) and text.strip():
            bits.append(f"[{attr}]\n{text.rstrip()}")
    exit_code = getattr(shell_out, "exit_code", None)
    if exit_code is not None:
        bits.append(f"[exitCode] {exit_code}")
    return "\n".join(bits) if bits else None


def reconstruct_run_transcript(turns: Iterable) -> str:
    """Rebuild a readable transcript (assistant text + tool I/O) from run.conversation().

    Used as the closeout sidecar body when ``RunResult.result`` is empty so the
    captured tool output — which IS the deliverable for run/verify dispatches — is
    not silently lost (friction 19819). ``RunResult`` carries no tool/stdout channel
    (cursor_sdk types.py RunResult), so the conversation is the only source. Tolerant
    of partially-parsed turns: never raises on an unexpected shape.
    """
    sections: list[str] = []
    for turn in turns or ():
        inner = getattr(turn, "turn", None)
        steps = getattr(inner, "steps", None)
        if steps:
            for step in steps:
                section = _conversation_step_section(step)
                if section:
                    sections.append(section)
            continue
        shell_section = _shell_turn_section(inner)
        if shell_section:
            sections.append(shell_section)
    return "\n\n".join(sections).strip()


def resolve_run_body(result_text: str, turns: Iterable) -> str:
    """Closeout body: the SDK terminal text when present, else a transcript
    reconstructed from the conversation so captured tool output is not lost
    (friction 19819). When neither is available the body is empty and
    ``empty_output_degraded_reason`` flags it PARTIAL.
    """
    if result_text and result_text.strip():
        return result_text
    return reconstruct_run_transcript(turns)
