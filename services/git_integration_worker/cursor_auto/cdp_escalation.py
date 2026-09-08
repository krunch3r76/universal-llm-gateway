"""CDP escalation commission — lane probe + Stargate team-dispatch HTTP.

Hop-cadence and Auto handler call ``read_cdp_lane_snapshot`` (shared transport
in ``cdp_ask.lane_snapshot``) for admission gating; the GET return is the
fire-time snap that predecessor capture later reads.
"""

from __future__ import annotations

from typing import Any

import httpx
from cdp_ask.lane_snapshot import read_cdp_lane_snapshot
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

__all__ = [
    "PROMPT_SOURCE_BODY",
    "PROMPT_SOURCE_BRIEF",
    "PROMPT_SOURCE_OVERRIDE",
    "PROMPT_SOURCE_URI",
    "commission_cdp_escalation",
    "escalation_lane_refusal",
    "load_advisor_brief",
    "read_cdp_lane_snapshot",
    "resolve_cdp_escalation_prompt",
]

_RELAY_TIMEOUT = 20.0

# Prompt source tags returned by resolve_cdp_escalation_prompt.
PROMPT_SOURCE_OVERRIDE = "prompt_override"
PROMPT_SOURCE_BRIEF = "advisor_brief"
PROMPT_SOURCE_URI = "prompt_uri"
PROMPT_SOURCE_BODY = "job.body"


def load_advisor_brief(prompt_uri: str) -> str:
    """Load a sealed advisor brief from a ``cortex://`` URI.

    Fail-closed: missing, empty, or non-cortex URIs raise ``ValueError`` so
    commission does not silently substitute ``job.body``.
    """
    if not prompt_uri.startswith("cortex://"):
        raise ValueError("prompt_uri must use cortex:// scheme")
    from implement_admission.closeout_helpers import cortex_files_root

    rel = prompt_uri.removeprefix("cortex://").lstrip("/")
    path = (cortex_files_root() / rel).resolve()
    root = cortex_files_root().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"prompt_uri {prompt_uri!r} escapes CORTEX_FILES_ROOT"
        ) from exc
    if not path.is_file():
        raise ValueError(f"prompt_uri not found: {prompt_uri!r} -> {path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"prompt_uri empty: {prompt_uri!r}")
    return text


def resolve_cdp_escalation_prompt(
    job: AutoJob,
    *,
    prompt_override: str | None = None,
    advisor_brief: str | None = None,
    prompt_uri: str | None = None,
) -> tuple[str, str]:
    """Choose the CDP advisor prompt; ``job.body`` is last-resort only.

    Precedence (first non-empty wins):
    1. ``prompt_override`` — hop-composed successor body
    2. ``advisor_brief`` kwarg, else ``job.advisor_brief``
    3. ``prompt_uri`` kwarg, else ``job.prompt_uri`` (loaded from cortex)
    4. ``job.body`` — executor DIRECTIVE fallback when no brief exists
    """
    if prompt_override is not None and prompt_override.strip():
        return prompt_override, PROMPT_SOURCE_OVERRIDE
    raw_brief = (
        advisor_brief
        if advisor_brief is not None
        else getattr(job, "advisor_brief", None)
    )
    if raw_brief and str(raw_brief).strip():
        return str(raw_brief), PROMPT_SOURCE_BRIEF
    uri = (prompt_uri or getattr(job, "prompt_uri", None) or "").strip()
    if uri:
        return load_advisor_brief(uri), PROMPT_SOURCE_URI
    return job.body, PROMPT_SOURCE_BODY


def escalation_lane_refusal(
    snap: dict[str, Any],
    *,
    unattended: bool,
    purpose: str | None = "ask",
) -> tuple[bool, str | None]:
    """Return ``(refuse, lane_label)`` for an escalation commission attempt.

    Purpose-aware Option A: advisor/escalation admits may use the reserved slot;
    transitional additive regime applies while ``seat_count`` exceeds the carve line.
    """
    from cdp_ask.lane_admission import escalation_lane_refusal as _purpose_refusal

    return _purpose_refusal(snap, unattended=unattended, purpose=purpose)


async def commission_cdp_escalation(
    job: AutoJob,
    *,
    model: str,
    reasoning_effort: str | None = None,
    stargate_url: str | None = None,
    purpose: str | None = None,
    mission_kind: str | None = None,
    parent_thread: str | None = None,
    prompt_override: str | None = None,
    advisor_brief: str | None = None,
    prompt_uri: str | None = None,
) -> dict[str, Any]:
    """POST one CDP generate leg to Stargate ``/api/v1/team/dispatch``.

    Uses the same async HTTP client pattern as ``services/mcp-server/tools/frontier.py``.

    *prompt_override* carries a body the caller composed for the successor (hop
    orientation) without mutating the queued job. A sealed advisor brief
    (``advisor_brief`` / ``prompt_uri`` / matching ``AutoJob`` fields) beats
    ``job.body``; ``job.body`` is used only when no brief exists.
    """
    try:
        prompt, prompt_source = resolve_cdp_escalation_prompt(
            job,
            prompt_override=prompt_override,
            advisor_brief=advisor_brief,
            prompt_uri=prompt_uri,
        )
    except ValueError as exc:
        logger.error("cdp escalation brief unreadable: %s", exc)
        return {
            "ok": False,
            "error": str(exc),
            "reason": "advisor_brief_unreadable",
        }
    logger.info(
        "cdp escalation prompt_source=%s job=%s",
        prompt_source,
        job.job_id,
    )
    body: dict[str, Any] = {
        "op": "generate",
        "model": model,
        "prompt": prompt,
        "dispatch_thread_id": job.thread_id,
        "contract": "none",
        "caller_agent": "cursor-auto",
    }
    if purpose:
        body["purpose"] = purpose
    if mission_kind:
        body["mission_kind"] = mission_kind
    if parent_thread:
        body["parent_thread"] = parent_thread
    effort = (reasoning_effort or "").strip().lower()
    if effort:
        body["reasoning_effort"] = effort

    endpoint = "/api/v1/team/dispatch"
    base = (stargate_url or DEFAULT_STARGATE_URL).rstrip("/")
    async with make_async_client(base, timeout=_RELAY_TIMEOUT) as client:
        try:
            resp = await client.post(endpoint, json=body)
        except httpx.RequestError as exc:
            logger.error("cdp escalation relay transport failure: %s", exc)
            return {"ok": False, "error": str(exc), "reason": "stargate_unreachable"}

    try:
        payload = resp.json()
    except ValueError:
        return {
            "ok": False,
            "status_code": resp.status_code,
            "error": "non_json_response",
        }

    if resp.status_code >= 400 or (isinstance(payload, dict) and payload.get("error")):
        return {
            "ok": False,
            "status_code": resp.status_code,
            "error": payload,
        }

    execution_id = ""
    if isinstance(payload, dict):
        execution_id = str(payload.get("execution_id") or "")
    logger.info(
        "cdp escalation commissioned job=%s model=%s execution_id=%s",
        job.job_id,
        model,
        execution_id,
    )
    return {
        "ok": True,
        "status_code": resp.status_code,
        "execution_id": execution_id,
        "dispatch": payload,
    }
