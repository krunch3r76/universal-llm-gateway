"""Pure helpers for skill-suggest dispatch — liftable into a pipeline handler later."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent_seat.registry import normalize_agent_slug
from implement_admission.spec import CloseoutStatus

_FENCED_JSON_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_ENVELOPE_KEYS = (
    "agent",
    "suggestions",
    "count",
    "omitted",
    "degraded_skills",
    "loaded_echo",
    "seat_preloaded",
    "ranker_status",
    "degraded",
)


def build_worker_message(
    *,
    loaded: list[str],
    conversation_context: str | None,
    agent: str,
    limit: int,
    all_candidates: list[dict[str, Any]],
) -> str:
    """Light-bounded worker instruction for LLM skill relevance ranking."""
    ctx_literal = json.dumps(conversation_context, ensure_ascii=False)
    candidates_payload = [
        {
            "slug": item.get("slug"),
            "description": item.get("description") or "",
            "trigger_short": item.get("trigger_short") or "",
            "stage_a_score": item.get("score", 0),
        }
        for item in all_candidates
        if isinstance(item, dict) and item.get("slug")
    ]
    candidates_json = json.dumps(candidates_payload, ensure_ascii=False, indent=2)
    output_contract = (
        "Emit ONLY one fenced ```json block containing a native skill_suggest "
        "envelope — no prose before or after the fence."
    )
    self_check = (
        "SELF-CHECK — mandatory before reporting done:\n"
        "1. Confirm exactly one ```json fence exists and parses as the native "
        "skill_suggest envelope.\n"
        "2. Confirm agent equals the requesting seat and count == len(suggestions).\n"
        "3. Confirm every suggestion slug appears in the provided candidates list.\n"
        "4. Confirm ranker_status is \"llm\" and each suggestion has "
        "reason_source=\"model\".\n"
        "Report each: PASS / FAIL + one-line evidence."
    )
    return (
        "Contract: light-bounded. Reason about skill relevance from the "
        "candidate set and conversation context; return a ranked native "
        "skill_suggest envelope.\n\n"
        f"REQUESTING SEAT: {json.dumps(agent)}\n"
        f"LOADED (exclude from suggestions): {json.dumps(loaded, ensure_ascii=False)}\n"
        f"CONVERSATION CONTEXT: {ctx_literal}\n"
        f"LIMIT: {limit}\n\n"
        f"CANDIDATES (slug must match exactly — do not invent slugs):\n"
        f"{candidates_json}\n\n"
        f"OUTPUT CONTRACT: {output_contract}\n"
        "Required envelope fields: agent, suggestions, count, omitted, "
        "degraded_skills, loaded_echo, seat_preloaded, ranker_status, degraded.\n"
        "Set ranker_status=\"llm\". For each suggestion set reason_source=\"model\" "
        "and a concise reason.\n\n"
        f"{self_check}"
    )


def resolve_workspaces_sidecar(ref: str, *, workspaces_root: Path) -> Path | None:
    """Resolve ``workspaces://universal-llm-gateway/…`` to a local file."""
    prefix = "workspaces://universal-llm-gateway/"
    if not ref.startswith(prefix):
        return None
    rel = ref[len(prefix) :].lstrip("/")
    candidate = (workspaces_root / rel).resolve()
    try:
        candidate.relative_to(workspaces_root.resolve())
    except ValueError:
        return None
    alt = (workspaces_root / "universal-llm-gateway" / rel).resolve()
    for path in (candidate, alt):
        if path.is_file():
            return path
    return None


def extract_last_fenced_json(text: str) -> dict[str, Any] | None:
    matches = list(_FENCED_JSON_RE.finditer(text))
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1].group(1).strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def validate_skill_suggest_envelope(
    envelope: dict[str, Any], *, canonical_agent: str
) -> bool:
    """Strict native-envelope validation — reject closeout-shaped or partial objects."""
    if not all(key in envelope for key in _ENVELOPE_KEYS):
        return False
    if normalize_agent_slug(str(envelope["agent"])) != normalize_agent_slug(
        canonical_agent
    ):
        return False
    suggestions = envelope["suggestions"]
    if not isinstance(suggestions, list):
        return False
    if envelope["count"] != len(suggestions):
        return False
    if not isinstance(envelope["omitted"], list):
        return False
    if not isinstance(envelope["degraded_skills"], list):
        return False
    if not isinstance(envelope["loaded_echo"], list):
        return False
    if not isinstance(envelope["seat_preloaded"], list):
        return False
    if not isinstance(envelope.get("ranker_status"), str):
        return False
    if not isinstance(envelope["degraded"], bool):
        return False
    if "degraded_reason" in envelope:
        degraded_reason = envelope["degraded_reason"]
        if degraded_reason is not None and not isinstance(degraded_reason, str):
            return False
    if "status" in envelope and envelope.get("status") in {
        CloseoutStatus.FAILED.value,
        CloseoutStatus.PARTIAL.value,
        CloseoutStatus.GATED.value,
    }:
        return False
    return True


def parse_envelope_from_closeout(
    closeout_body: str,
    *,
    canonical_agent: str,
    workspaces_root: Path,
) -> dict[str, Any] | None:
    """Closeout turn → sidecar → last fenced json → validated envelope."""
    try:
        closeout = json.loads(closeout_body)
    except json.JSONDecodeError:
        return None
    if not isinstance(closeout, dict):
        return None
    status = str(closeout.get("status") or "").lower()
    if status == CloseoutStatus.FAILED.value:
        return None
    evidence = closeout.get("evidence_uris") or {}
    if not isinstance(evidence, dict):
        return None
    paths = evidence.get("artifact_paths") or []
    if not paths or not isinstance(paths[0], str):
        return None
    sidecar_path = resolve_workspaces_sidecar(paths[0], workspaces_root=workspaces_root)
    if sidecar_path is None:
        return None
    try:
        sidecar_text = sidecar_path.read_text(encoding="utf-8")
    except OSError:
        return None
    envelope = extract_last_fenced_json(sidecar_text)
    if envelope is None:
        return None
    if not validate_skill_suggest_envelope(envelope, canonical_agent=canonical_agent):
        return None
    return envelope
