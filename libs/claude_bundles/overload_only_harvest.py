"""Detect Anthropic API error-banner-only harvest bodies.

A generate tracker archive that is only ``API Error: 529/500/…`` does not prove
the Cowork CSE is empty — scrape that generate's ``chat_url`` (a:30408 / a:30411).
Legitimate short answers that merely quote an API error must not match.
"""

from __future__ import annotations

import re

# 500 = server error banner in-chat; 503/529 = overload; 523 kept from prior gate.
_ERROR_BANNER_CODE = r"(?:500|503|52[39])"
_ERROR_BANNER_BODY_RE = re.compile(rf"API Error:\s*{_ERROR_BANNER_CODE}", re.IGNORECASE)
_ERROR_BANNER_LINE_RE = re.compile(
    rf"^(?:Claude responded: )?API Error:\s*{_ERROR_BANNER_CODE}.*$",
    re.IGNORECASE,
)
_ERROR_BANNER_ONLY_MAX_LEN = 500


def is_error_banner_only_harvest(body: str) -> bool:
    """True when every non-empty line is an API error banner and the body is short.

    Requires every non-empty line to be error-banner shaped so a legitimate
    short harvest that merely quotes ``API Error: 529`` is not treated as empty.
    """
    text = body.strip()
    if not text or len(text) > _ERROR_BANNER_ONLY_MAX_LEN:
        return False
    if not _ERROR_BANNER_BODY_RE.search(text):
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not _ERROR_BANNER_LINE_RE.match(stripped):
            return False
    return True
