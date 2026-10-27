"""Draft parsing and sentence splitting for provenance checks."""

from __future__ import annotations

import json
import re
from typing import Any

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_NAME_STOP = frozenset(
    {
        "The",
        "This",
        "That",
        "Could",
        "Would",
        "Please",
        "Dear",
        "And",
        "But",
        "So",
        "It",
        "When",
        "Do",
        "Does",
        "Did",
        "Can",
        "Will",
        "Is",
        "Are",
        "Was",
        "Were",
        "Have",
        "Has",
        "Had",
        "Would",
        "Could",
        "Should",
        "Not",
        "Hi",
        "Hello",
    }
)
_ENVELOPE_OR_EXHIBIT_LINE_RE = re.compile(
    r"^\s*(?:(?:to|cc|bcc|from|subject|re|date)\s*:|Exhibit\s+\d+\s*:|"
    r"(?:thank you|thanks|sincerely|regards|best regards|kind regards|respectfully)[,!]?\s*$)",
    re.IGNORECASE,
)
_VALEDICTION_LINE_RE = re.compile(
    r"^\s*(?:thank you|thanks|sincerely|regards|best regards|kind regards|respectfully)[,!]?\s*$",
    re.IGNORECASE,
)


def _parse_draft(draft_json: Any) -> dict[str, Any] | None:
    if isinstance(draft_json, str):
        text = draft_json.strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                draft_json = json.loads(text)
            except json.JSONDecodeError:
                return {"draft": text, "claims": [], "need": [], "dispositions": []}
        else:
            return {"draft": text, "claims": [], "need": [], "dispositions": []}
    if not isinstance(draft_json, dict):
        return None
    if "draft" not in draft_json and isinstance(draft_json.get("text"), str):
        return {
            "draft": draft_json["text"],
            "claims": [],
            "need": [],
            "dispositions": [],
        }
    if "draft" not in draft_json:
        return None
    if isinstance(draft_json.get("draft"), str):
        out = dict(draft_json)
        out.setdefault("claims", [])
        return out
    return None


def _sentences(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i].strip()
        if not raw_line:
            if current:
                blocks.append(" ".join(current))
                current = []
            i += 1
            continue
        if _VALEDICTION_LINE_RE.match(raw_line):
            if current:
                blocks.append(" ".join(current))
                current = []
            closing = [raw_line]
            if i + 1 < len(lines) and lines[i + 1].strip():
                closing.append(lines[i + 1].strip())
                i += 1
            blocks.append("\n".join(closing))
            i += 1
            continue
        if _ENVELOPE_OR_EXHIBIT_LINE_RE.match(raw_line):
            if current:
                blocks.append(" ".join(current))
                current = []
            blocks.append(raw_line)
            i += 1
            continue
        current.append(raw_line)
        i += 1
    if current:
        blocks.append(" ".join(current))
    out: list[str] = []
    for block in blocks:
        if "\n" in block or _ENVELOPE_OR_EXHIBIT_LINE_RE.match(block):
            out.append(block.strip())
        else:
            for part in _SENTENCE.split(block):
                if part.strip():
                    out.append(part.strip())
    return out
