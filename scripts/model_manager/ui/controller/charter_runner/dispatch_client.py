"""Admit a charter window via Stargate team dispatch or attended handoff.

Writes a Resume-step-0 packet under the gateway checkout, then either:

- ``POST /api/v1/team/dispatch`` (default generate: Grok cursor-sdk), or
- ``POST /api/v1/team/handoff`` (attended: ``role=cursor-consult`` + packet_path).

Response carries ``thread_id`` / ``execution_id`` for the worker surface + transcript.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from transport_utils import DEFAULT_STARGATE_URL, make_async_client

from .executor_defaults import default_generate_body, default_handoff_body

AdmissionMode = Literal["generate", "handoff"]

_TIMEOUT_S = 30.0
_DISPATCH_PATH = "/api/v1/team/dispatch"
_HANDOFF_PATH = "/api/v1/team/handoff"
_CALLER = "charter-runner"
_PACKET_DIR = Path("tmp/charter-runner")


def write_handoff_packet(
    workspace_root: Path, root_id: str, window_index: int, packet_text: str
) -> str:
    """Persist packet; return repo-relative path for dispatch ``packet_path``."""
    dest_dir = workspace_root / _PACKET_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{root_id}-w{window_index}.md"
    (dest_dir / filename).write_text(packet_text, encoding="utf-8")
    return f"universal-llm-gateway/{_PACKET_DIR.as_posix()}/{filename}"


async def fire_window(
    root_id: str,
    packet_text: str,
    *,
    workspace_root: Path,
    window_index: int = 1,
    subject: str | None = None,
    admission_mode: AdmissionMode = "generate",
) -> dict[str, Any]:
    """Admit one charter window (generate default or attended handoff)."""
    packet_path = write_handoff_packet(
        workspace_root, root_id, window_index, packet_text
    )
    subj = subject or (
        f"Charter-runner window {window_index} — agent-bus:{root_id}"
    )
    if admission_mode == "handoff":
        body = default_handoff_body(
            root_id=root_id,
            window_index=window_index,
            packet_path=packet_path,
            subject=subj,
            caller_agent=_CALLER,
        )
        path = _HANDOFF_PATH
    else:
        body = default_generate_body(
            root_id=root_id,
            window_index=window_index,
            packet_path=packet_path,
            subject=subj,
            caller_agent=_CALLER,
        )
        path = _DISPATCH_PATH
    async with make_async_client(DEFAULT_STARGATE_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.post(path, json=body)
        resp.raise_for_status()
        result = dict(resp.json())
    if "thread_id" not in result and result.get("thread"):
        result["thread_id"] = result["thread"]
    result.setdefault(
        "dispatch_id",
        str(result.get("execution_id") or result.get("thread_id") or ""),
    )
    result["packet_path"] = packet_path
    if admission_mode == "handoff":
        result["executor"] = {"role": "cursor-consult", "seat": "cursor"}
    else:
        result["executor"] = {
            "seat": body["seat"],
            "model": body["model"],
            "model_knobs": body["model_knobs"],
            "contract": body["contract"],
        }
    return result
