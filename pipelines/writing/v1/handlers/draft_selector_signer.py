"""Signer band scoring for draft_selector."""

from __future__ import annotations

import re
from typing import Any

_DECLARANT = re.compile(r"\bI,\s+([^,\n]{2,80}),\s+state\b")


def requested_signer(assemble_json: dict[str, Any]) -> str:
    """Return the signer the caller asked for, or an empty string."""
    intake = assemble_json.get("intake")
    brief = assemble_json.get("brief")
    sources = [
        assemble_json.get("signer"),
        intake.get("signer") if isinstance(intake, dict) else None,
        brief.get("signer") if isinstance(brief, dict) else None,
    ]
    for source in sources:
        if isinstance(source, str) and source.strip():
            return source.strip()
    block = str(assemble_json.get("brief_block") or "")
    match = re.search(r"(?m)^Signer:[ \t]*([^\n]*?)[ \t]*$", block)
    if not match:
        return ""
    token = match.group(1).strip()
    if not token or token.casefold() == "none":
        return ""
    return token


def signer_verbatim_required(
    attested_pins: list[dict[str, Any]],
    *,
    signer: str,
) -> bool:
    if not signer.strip():
        return False
    signer_re = re.compile(rf"\b{re.escape(signer.strip())}\b", re.I)
    for pin in attested_pins:
        if pin.get("verbatim_required") is not True:
            continue
        excerpt = str(pin.get("excerpt") or "")
        if signer_re.search(excerpt):
            return True
    return False


def signer_band(
    draft_text: str,
    signer: str,
    *,
    channel: str = "",
    signer_verbatim_required_flag: bool = False,
) -> int:
    if not signer:
        return 0
    if channel == "sms" and not signer_verbatim_required_flag:
        declarant = _DECLARANT.search(draft_text)
        if declarant and signer.casefold() not in declarant.group(1).casefold():
            return -1
        return 0
    if re.search(rf"\b{re.escape(signer)}\b", draft_text, re.I):
        return 1
    declarant = _DECLARANT.search(draft_text)
    if declarant and signer.casefold() not in declarant.group(1).casefold():
        return -1
    return 0
