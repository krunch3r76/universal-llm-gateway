"""CDP digest extract — seal prompt, satellite submit/poll, harvest parse."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

import httpx
from universal_logging import get_logger

from .dispatch_ops._pinned_deliverable import (
    pinned_deliverable_uri,
    write_pinned_deliverable_impl,
)
from .dispatch_ops._shared import _FILES_ROOT
from .journal_digest_extract import (
    _EXTRACT_SYSTEM,
    parse_claim_batch,
    strip_json_fences,
)

logger = get_logger("cortex-api.journal_digest_extract_cdp")

PROMPT_REV_SOFT_V2 = "soft-v2"
_DEFAULT_CDP_MODEL = "haiku-4.5"
_DEFAULT_TIMEOUT_S = 900
_POLL_INTERVAL_S = 2.0

_SOFT_V2_FRAMING = """\
# Journal claim extraction task

Extract atomic knowledge-graph claims from the journal ENTRY TEXT below.

Use the classification rules and JSON schema in the system instructions.
Reply with a single JSON object only (you may wrap it in a ```json fence).
"""


def cdp_model() -> str:
    return os.environ.get("CORTEX_DIGEST_CDP_MODEL", _DEFAULT_CDP_MODEL).strip()


def cdp_project_uuid() -> str:
    return os.environ.get("CORTEX_DIGEST_CDP_PROJECT_UUID", "").strip()


def project_ask_url() -> str:
    return os.environ.get("PROJECT_ASK_URL", "").strip().rstrip("/")


def cdp_timeout_s() -> int:
    raw = os.environ.get("CORTEX_DIGEST_CDP_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S))
    return int(raw)


def prompt_sha256(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_soft_v2_sealed_prompt(
    *,
    entry_text: str,
    entry_anchor: str,
    journal_uri: str,
) -> str:
    """Soft task framing (v2) + extract system semantics + entry corpus."""
    return (
        f"{_SOFT_V2_FRAMING}\n"
        f"{_EXTRACT_SYSTEM}\n\n"
        f"Journal URI: {journal_uri}\n"
        f"Entry anchor: {entry_anchor}\n\n"
        f"## ENTRY TEXT\n\n{entry_text.strip()}\n"
    )


def write_job_prompt(job_id: str, prompt_text: str) -> dict[str, Any]:
    rel = f"ephemeral/digest/{job_id}-prompt.md"
    result = write_pinned_deliverable_impl(rel, prompt_text)
    if "error" in result:
        return result
    result["prompt_uri"] = pinned_deliverable_uri(rel)
    result["prompt_sha256"] = prompt_sha256(prompt_text)
    return result


def digest_archive_rel(job_id: str) -> str:
    return f"ephemeral/digest/{job_id}-archive.md"


def submit_cdp_execution(
    *,
    prompt_uri: str,
    model: str,
    project_uuid: str,
    timeout_s: int,
    archive_path: str | None = None,
) -> dict[str, Any]:
    base = project_ask_url()
    if not base:
        return {"error": "PROJECT_ASK_URL not configured"}
    if not project_uuid:
        return {"error": "CORTEX_DIGEST_CDP_PROJECT_UUID not configured"}

    body = {
        "prompt_uri": prompt_uri,
        "model": model,
        "project_uuid": project_uuid,
        "converse": False,
        "holder": "cortex-digest",
        "purpose": "digest-extract",
        "timeout_s": timeout_s,
    }
    if archive_path:
        body["archive_path"] = archive_path
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{base}/v1/project-ask/executions", json=body)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("CDP digest submit failed", exc_info=True)
        return {"error": str(exc)}


def poll_cdp_execution(
    execution_id: str,
    *,
    requested_model: str,
    timeout_s: int,
) -> dict[str, Any]:
    base = project_ask_url()
    if not base:
        return {"error": "PROJECT_ASK_URL not configured", "terminal": True}

    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(f"{base}/v1/project-ask/executions/{execution_id}")
                resp.raise_for_status()
                last = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {
                    "error": "execution_not_found",
                    "terminal": True,
                    "resubmit": True,
                }
            logger.warning("CDP digest poll HTTP error", exc_info=True)
            return {"error": str(exc), "terminal": True}
        except Exception as exc:
            logger.warning("CDP digest poll failed", exc_info=True)
            return {"error": str(exc), "terminal": True}

        status = last.get("status")
        if status in ("completed", "failed", "aborted"):
            if status != "completed" or not last.get("ok"):
                return {
                    **last,
                    "error": last.get("error") or status,
                    "terminal": True,
                }
            attested = (last.get("attested_model") or "").strip()
            if attested and not _model_matches(requested_model, attested):
                return {
                    **last,
                    "error": "model_unavailable",
                    "terminal": True,
                    "park_reason": "model_unavailable",
                }
            archive_uri = last.get("archive_uri")
            if not archive_uri:
                return {
                    **last,
                    "error": "empty_harvest",
                    "terminal": True,
                    "park_reason": "empty_harvest",
                }
            return {**last, "terminal": True}
        time.sleep(_POLL_INTERVAL_S)

    return {"error": "timeout", "terminal": True, "park_reason": "timeout", **last}


def _model_matches(requested: str, attested: str) -> bool:
    req = requested.strip().lower()
    got = attested.strip().lower()
    if not req or not got:
        return False
    if req in got or got in req:
        return True
    req_family = req.split("-", 1)[0]
    return req_family in got


def read_archive_body(archive_uri: str) -> str | None:
    from .dispatch_ops._pinned_deliverable import normalize_cortex_rel

    rel = normalize_cortex_rel(archive_uri)
    if rel is None:
        return None
    path = (_FILES_ROOT / rel).resolve()
    try:
        path.relative_to(_FILES_ROOT.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def harvest_sha256(body: str) -> str:
    return prompt_sha256(body)


def _body_from_cdp_archive(harvest_body: str) -> str:
    """Prefer ``## Body`` section; drop CDP harvest chrome before JSON parse."""
    marker = "## Body"
    idx = harvest_body.find(marker)
    body = harvest_body[idx + len(marker) :] if idx >= 0 else harvest_body
    return body.strip()


def _extract_json_object(text: str) -> str:
    """Return the first top-level ``{...}`` span (handles fence/chrome noise)."""
    cleaned = strip_json_fences(text)
    start = cleaned.find("{")
    if start < 0:
        return cleaned
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(cleaned[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : i + 1]
    return cleaned[start:]


def parse_harvest_claims(
    harvest_body: str,
    *,
    entry_anchor: str,
    journal_uri: str,
) -> dict[str, Any] | None:
    body = _body_from_cdp_archive(harvest_body)
    cleaned = _extract_json_object(body)
    return parse_claim_batch(
        cleaned,
        entry_anchor=entry_anchor,
        journal_uri=journal_uri,
    )


def run_cdp_extract_for_job(
    job: dict[str, Any],
) -> dict[str, Any]:
    """Submit + poll + parse for one extract job. Caller owns state transitions."""
    model = job.get("model") or cdp_model()
    timeout_s = cdp_timeout_s()
    journal_uri = job.get("journal_uri") or ""
    prompt_text = build_soft_v2_sealed_prompt(
        entry_text=str(job["entry_text"]),
        entry_anchor=str(job["entry_anchor"]),
        journal_uri=journal_uri,
    )
    prompt_write = write_job_prompt(str(job["job_id"]), prompt_text)
    if "error" in prompt_write:
        return prompt_write

    submit = submit_cdp_execution(
        prompt_uri=str(prompt_write["prompt_uri"]),
        model=model,
        project_uuid=cdp_project_uuid(),
        timeout_s=timeout_s,
        archive_path=digest_archive_rel(str(job["job_id"])),
    )
    if "error" in submit:
        return submit

    execution_id = submit.get("execution_id")
    if not execution_id:
        return {"error": "missing execution_id from submit"}

    poll = poll_cdp_execution(
        str(execution_id),
        requested_model=model,
        timeout_s=timeout_s,
    )
    if poll.get("park_reason") == "model_unavailable":
        return poll
    if poll.get("error") and poll.get("terminal"):
        return poll

    archive_uri = poll.get("archive_uri")
    if not archive_uri:
        return {"error": "empty_harvest", "park_reason": "empty_harvest"}

    body = read_archive_body(str(archive_uri))
    if not body:
        return {"error": "harvest_unreachable", "park_reason": "harvest_unreachable"}

    claims = parse_harvest_claims(
        body,
        entry_anchor=str(job["entry_anchor"]),
        journal_uri=journal_uri,
    )
    if claims is None:
        return {"error": "parse_failed", "park_reason": "prose_only"}

    return {
        "execution_id": execution_id,
        "prompt_uri": prompt_write["prompt_uri"],
        "prompt_sha256": prompt_write["prompt_sha256"],
        "archive_uri": archive_uri,
        "harvest_sha256": harvest_sha256(body),
        "attested_model": poll.get("attested_model"),
        "claims_json": claims,
    }
