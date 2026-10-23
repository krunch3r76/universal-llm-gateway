"""Promote-time sidecar parsing: title, last-4 account resolution, graph wiring.

Spec: friction:39984 — document entity naming from sidecar title; link accounts
at promote with relationship + account assertion citing the evidence sidecar.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from ._cortex_relay import cx

logger = get_logger(__name__)

_LAST4_MASKED = re.compile(
    r"(?:\.{2,}|…+|(?:\*|#|x|X){1,4})\s*(\d{4})\b",
    re.IGNORECASE,
)
_LAST4_ENDING_IN = re.compile(
    r"ending\s+in\s+(\d{4})\b",
    re.IGNORECASE,
)
_ATX_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)
_GENERIC_ATX_HEADINGS = frozenset(
    {
        "confirmation",
        "receipt",
        "statement",
        "invoice",
        "document",
        "notice",
    }
)
_BOLD_SPAN = re.compile(r"\*\*([^*]+)\*\*")


def sidecar_body_after_frontmatter(content: str) -> str:
    """Return markdown body after a leading ``---`` frontmatter block."""
    if not content.startswith("---\n"):
        return content.strip()
    end_marker = content.find("\n---\n", 4)
    if end_marker < 0:
        return content.strip()
    return content[end_marker + 5 :].strip()


def _heading_tokens(heading: str) -> list[str]:
    return [t.lower() for t in _TOKEN_SPLIT.split(heading.strip()) if len(t) >= 2]


def _is_generic_heading(heading: str) -> bool:
    tokens = _heading_tokens(heading)
    if len(tokens) < 2:
        if len(tokens) == 1 and tokens[0] in _GENERIC_ATX_HEADINGS:
            return True
        return len(tokens) == 0
    return False


def _counterparty_from_body(body: str) -> str | None:
    for match in _BOLD_SPAN.finditer(body):
        span = match.group(1).strip()
        lowered = span.lower()
        if "wells fargo" in lowered:
            return "Wells Fargo"
        if len(_heading_tokens(span)) >= 2:
            return span
    wf = re.search(r"\b(Wells Fargo(?:\s+[A-Za-z®]+)*)\b", body, re.IGNORECASE)
    if wf:
        return wf.group(1).strip()
    return None


def _enrich_generic_heading(
    heading: str,
    body: str,
    fallback_stem: str | None,
) -> str:
    if fallback_stem:
        stem = fallback_stem.strip().replace("_", " ")
        if len(_heading_tokens(stem)) >= 2:
            return stem
    counterparty = _counterparty_from_body(body)
    if counterparty:
        if counterparty.lower() == heading.strip().lower():
            return counterparty
        return f"{heading.strip()} - {counterparty}"
    if fallback_stem and fallback_stem.strip():
        return fallback_stem.strip().replace("_", " ")
    return heading.strip()


def title_from_sidecar_content(
    content: str,
    fallback_stem: str | None = None,
) -> str | None:
    """Derive a human document title from sidecar markdown (ATX heading first)."""
    body = sidecar_body_after_frontmatter(content)
    if not body:
        return None
    match = _ATX_TITLE.search(body)
    if match:
        title = match.group(1).strip()
        if title and _is_generic_heading(title):
            return _enrich_generic_heading(title, body, fallback_stem)
        return title or None
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if _is_generic_heading(stripped):
                return _enrich_generic_heading(stripped, body, fallback_stem)
            return stripped
    return None


def extract_last4_digits(sidecar_text: str) -> set[str]:
    """Collect masked last-4 account suffixes from sidecar body text."""
    found: set[str] = set()
    for pattern in (_LAST4_MASKED, _LAST4_ENDING_IN):
        for match in pattern.finditer(sidecar_text):
            digits = match.group(1)
            if digits.isdigit():
                found.add(digits)
    return found


def _account_last4(entity: dict[str, Any]) -> str | None:
    attrs_raw = entity.get("attributes")
    attrs: dict[str, Any] = {}
    if isinstance(attrs_raw, dict):
        attrs = attrs_raw
    elif isinstance(attrs_raw, str) and attrs_raw.strip():
        try:
            parsed = json.loads(attrs_raw)
            if isinstance(parsed, dict):
                attrs = parsed
        except json.JSONDecodeError:
            pass
    last4 = attrs.get("last4")
    if last4 is not None:
        text = str(last4).strip()
        if len(text) == 4 and text.isdigit():
            return text
    eid = str(entity.get("id") or "")
    if eid.startswith("account:") and "-" in eid:
        suffix = eid.rsplit("-", 1)[-1]
        if len(suffix) == 4 and suffix.isdigit():
            return suffix
    return None


def resolve_account_ids_for_last4(last4: str) -> list[str]:
    """Return ``account:*`` entity ids matching ``last4`` via cortex list."""
    result = cx(
        "POST",
        "/dispatch",
        {"tool": "entities", "arguments": {"type": "account", "limit": 200}},
    )
    if result.get("error"):
        logger.warning(
            "promote account link: entities list failed for last4=%s: %s",
            last4,
            result.get("error"),
        )
        return []
    matches: list[str] = []
    for row in result.get("items", []):
        if _account_last4(row) == last4:
            eid = row.get("id")
            if eid:
                matches.append(str(eid))
    return matches


def _relationship_create(document_id: str, account_id: str, *, sidecar_uri: str) -> None:
    result = cx(
        "POST",
        "/dispatch",
        {
            "tool": "relationship_create",
            "arguments": {
                "source_id": document_id,
                "target_id": account_id,
                "type_id": "related_to",
                "evidence": sidecar_uri,
            },
        },
    )
    if result.get("error"):
        logger.warning(
            "promote account link: relationship_create %s -> %s failed: %s",
            document_id,
            account_id,
            result.get("error"),
        )


def _assert_account_observation(
    account_id: str,
    *,
    sidecar_uri: str,
    claim: str,
) -> None:
    result = cx(
        "POST",
        "/dispatch",
        {
            "tool": "assert",
            "arguments": {
                "entity_id": account_id,
                "claim": claim,
                "confidence": "believed",
                "evidence": sidecar_uri,
                "evidence_uris": [sidecar_uri],
                "derivation_type": "agent_observation",
                "seeded_by": "ingest_promote",
            },
        },
        dispatch_tool="assert",
    )
    if result.get("error"):
        logger.warning(
            "promote account link: assert on %s failed: %s",
            account_id,
            result.get("error"),
        )


def _summarize_sidecar_for_claim(sidecar_text: str, *, document_name: str) -> str:
    body = sidecar_body_after_frontmatter(sidecar_text)
    snippet = " ".join(body.split())[:240]
    if snippet:
        return f"Promoted document {document_name!r} cites this account ({snippet})"
    return f"Promoted document {document_name!r} references this account."


def link_promoted_document_to_accounts(
    *,
    document_entity_id: str,
    sidecar_path: Path,
    sidecar_uri: str,
    document_name: str,
) -> dict[str, Any]:
    """Resolve last-4 values in the sidecar to accounts; wire edges + assertions.

    Never raises — partial failure is logged and returned in the summary dict.
    """
    summary: dict[str, Any] = {
        "last4_found": [],
        "accounts_linked": [],
        "accounts_asserted": [],
        "errors": [],
    }
    try:
        sidecar_text = sidecar_path.read_text(encoding="utf-8")
    except OSError as exc:
        summary["errors"].append(f"sidecar_unreadable:{exc}")
        return summary

    last4s = sorted(extract_last4_digits(sidecar_text))
    summary["last4_found"] = last4s
    if not last4s:
        return summary

    claim = _summarize_sidecar_for_claim(sidecar_text, document_name=document_name)
    linked: set[str] = set()
    for digits in last4s:
        for account_id in resolve_account_ids_for_last4(digits):
            if account_id in linked:
                continue
            linked.add(account_id)
            _relationship_create(document_entity_id, account_id, sidecar_uri=sidecar_uri)
            summary["accounts_linked"].append(account_id)
            _assert_account_observation(
                account_id,
                sidecar_uri=sidecar_uri,
                claim=claim,
            )
            summary["accounts_asserted"].append(account_id)
    return summary
