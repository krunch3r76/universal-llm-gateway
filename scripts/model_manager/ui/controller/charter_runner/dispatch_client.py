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
from universal_logging import get_logger

from .executor_defaults import (
    autonomous_generate_body,
    consult_host_generate_body,
    default_handoff_body,
    default_judgment_body,
    implement_body,
    operator_proxy_host_generate_body,
)

logger = get_logger(__name__)

AdmissionMode = Literal["generate", "handoff", "autonomous", "consult", "operator_proxy"]

_TIMEOUT_S = 30.0
_DISPATCH_PATH = "/api/v1/team/dispatch"
_HANDOFF_PATH = "/api/v1/team/handoff"
_CALLER = "charter-runner"
_PACKET_DIR = Path("tmp/charter-runner")


def write_handoff_packet(
    workspace_root: Path, root_id: str, window_index: int, packet_text: str
) -> str:
    """Persist packet; return path for dispatch ``packet_path``.

    Path is **source-repo-relative** (``tmp/charter-runner/…``), not
    workspaces-prefixed (``universal-llm-gateway/tmp/…``). Stargate resolves
    both via ``repo_base``; the cursor-sdk worker only joins onto
    ``source_repo`` and rejects the double-prefixed form
    (``CURSOR_PACKET_INVALID``).
    """
    dest_dir = workspace_root / _PACKET_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{root_id}-w{window_index}.md"
    (dest_dir / filename).write_text(packet_text, encoding="utf-8")
    return f"{_PACKET_DIR.as_posix()}/{filename}"


async def fire_window(
    root_id: str,
    packet_text: str,
    *,
    workspace_root: Path,
    window_index: int = 1,
    subject: str | None = None,
    admission_mode: AdmissionMode = "generate",
    consult_role: str | None = None,
    implement_source_ref: str | None = None,
    work_key: str | None = None,
) -> dict[str, Any]:
    """Admit one charter window (generate default or attended handoff).

    ``implement_source_ref`` set (autonomous mode only) fires the mechanical
    Composer implement bind instead of the Grok judgment bind; the caller is
    ``executor_routing``, which supplies a ref only when the implement lane is
    proven.

    ``work_key`` is embedded in the packet body (``work_key: …`` line) — GIW
    parses it from packet text. It must **not** be sent as a team_dispatch
    generate field (Stargate rejects unknown keys with 400 validation_failed).
    """
    if work_key:
        packet_text = f"work_key: {work_key}\n\n{packet_text}"
    packet_path = write_handoff_packet(
        workspace_root, root_id, window_index, packet_text
    )
    subj = subject or (f"Charter-runner window {window_index} — agent-bus:{root_id}")
    if admission_mode == "handoff":
        body = default_handoff_body(
            root_id=root_id,
            window_index=window_index,
            packet_path=packet_path,
            subject=subj,
            caller_agent=_CALLER,
        )
        path = _HANDOFF_PATH
    elif admission_mode == "consult":
        # Both judgment_gap and r_admit: cursor-sdk host → CDP auto-wake.
        # Handoff web-consult (push_reminder) left the autonomous tick hung
        # (a:26476) — host generate is the on-tick wire.
        body = consult_host_generate_body(
            root_id=root_id,
            window_index=window_index,
            packet_path=packet_path,
            subject=subj,
            caller_agent=_CALLER,
        )
        path = _DISPATCH_PATH
    elif admission_mode == "autonomous":
        if implement_source_ref:
            body = implement_body(
                root_id=root_id,
                window_index=window_index,
                packet_path=packet_path,
                subject=subj,
                caller_agent=_CALLER,
                source_ref=implement_source_ref,
            )
        else:
            body = autonomous_generate_body(
                root_id=root_id,
                window_index=window_index,
                packet_path=packet_path,
                subject=subj,
                caller_agent=_CALLER,
            )
        path = _DISPATCH_PATH
    elif admission_mode == "operator_proxy":
        body = operator_proxy_host_generate_body(
            root_id=root_id,
            window_index=window_index,
            packet_path=packet_path,
            subject=subj,
            caller_agent=_CALLER,
        )
        path = _DISPATCH_PATH
    else:
        body = default_judgment_body(
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
    if work_key:
        result["work_key"] = work_key
    if "thread_id" not in result and result.get("thread"):
        result["thread_id"] = result["thread"]
    result.setdefault(
        "dispatch_id",
        str(result.get("execution_id") or result.get("thread_id") or ""),
    )
    result["packet_path"] = packet_path
    if admission_mode == "handoff":
        result["executor"] = {"role": "cursor-consult", "seat": "cursor"}
    elif admission_mode == "consult":
        role = (consult_role or "judgment_gap").strip().lower() or "judgment_gap"
        result["executor"] = {
            "seat": body["seat"],
            "model": body["model"],
            "model_knobs": body["model_knobs"],
            "contract": body["contract"],
            "consult_role": role,
            "reviewer_model": "cdp/opus-5",
        }
    else:
        result["executor"] = {
            "seat": body["seat"],
            "model": body["model"],
            "model_knobs": body["model_knobs"],
            "contract": body["contract"],
        }
    _warn_on_ungated_implement(root_id, body, result)
    return result


def _warn_on_ungated_implement(
    root_id: str, body: dict[str, Any], result: dict[str, Any]
) -> None:
    """Flag an implement window whose readiness gate silently no-opped.

    ``require_implement_ready`` short-circuits when it cannot resolve a todo
    ``source_ref`` from packet front matter, and it does so *without* failing
    the dispatch — the window just runs unreviewed. A server-stamped
    ``implement_spec_hash`` is the proof the gate actually ran, so its absence
    on an implement dispatch is a defect, never a success (review §1 / F5).
    """
    if body.get("contract") != "implement":
        return
    if result.get("implement_spec_hash"):
        return
    logger.error(
        "charter-runner implement window fired without implement_spec_hash "
        "root=%s dispatch=%s source_ref=%s — require_implement_ready no-opped; "
        "treat this window's output as ungated",
        root_id,
        result.get("dispatch_id"),
        body.get("source_ref"),
    )
    result["implement_gate_bypassed"] = True
