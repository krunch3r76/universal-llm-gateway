"""Ground outbound drafts against the verbatim inbound ``source_message``.

``writing_assemble_v1`` attaches the inbound block to every drafter prompt.
``writing_draft_selector_v1`` calls ``unsupported_source_claims`` to drop drafts
that invent missing links when the source text contains no URL.
"""

from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://|\bwww\.", re.I)

_LINK_MISSING_CLAIM = re.compile(
    r"(?:"
    r"link\s+(?:did\s+not|didn't|never)\s+come\s+through"
    r"|(?:application|apply)\s+link\s+(?:did\s+not|didn't|never|was\s+not)"
    r"|send\s+(?:me\s+)?(?:the\s+)?(?:application|apply)\s+link"
    r"|(?:could|would)\s+you\s+send\s+(?:me\s+)?(?:the\s+)?(?:application|apply)\s+link"
    r")",
    re.I,
)


def source_message_has_url(source_message: str) -> bool:
    """Return whether ``source_message`` contains an http(s) or www URL."""
    return bool(_URL_RE.search(source_message or ""))


def unsupported_source_claims(
    *,
    source_message: str,
    draft_text: str = "",
    brief_text: str = "",
) -> list[str]:
    """List premise violations when the inbound message has no URL.

    Flags link-missing or application-link requests in ``draft_text`` or
    ``brief_text`` that the source cannot support. Returns an empty list when
    ``source_message`` is blank or contains a URL, or when no violating claim
    appears. Does not perform I/O.
    """
    source = (source_message or "").strip()
    if not source or source_message_has_url(source):
        return []
    violations: list[str] = []
    for label, text in (("draft", draft_text), ("brief", brief_text)):
        body = (text or "").strip()
        if not body:
            continue
        if _LINK_MISSING_CLAIM.search(body):
            violations.append(f"{label}:link_missing_claim_without_source_url")
    return violations
