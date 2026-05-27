"""
Markdown fence stripping for cloud model JSON responses.

Cloud providers (notably Anthropic) may wrap JSON in markdown fences
(```json ... ```) or precede it with conversational preamble even when
``response_format=json_object`` is requested. ``_strip_markdown_fence``
peels the JSON payload out so ``json.loads`` succeeds; on no match it
returns the stripped input unchanged so the caller's ``json.loads`` can
surface the original decode error.
"""

from __future__ import annotations


def _strip_markdown_fence(text: str) -> str:
    """Extract JSON content from model responses.

    Cloud providers (notably Anthropic) may return JSON wrapped in
    markdown fences and/or preceded by preamble text ("Let me analyze...")
    even when response_format: json_object was requested. This extracts
    the JSON object so json.loads succeeds.

    Extraction order:
    1. If text starts with ```, strip the fence
    2. If a fenced JSON block appears anywhere, extract it
    3. If a bare JSON object ({...}) appears after preamble, extract it
    4. Return stripped text as-is (let json.loads report the error)
    """
    import re

    stripped = text.strip()

    if stripped.startswith("```"):
        match = re.match(r"```(?:json|\w*)\s*\n(.*)```", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()

    fence_match = re.search(r"```(?:json|\w*)\s*\n(.*?)```", stripped, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()

    brace_match = re.search(r"(\{.*\})", stripped, re.DOTALL)
    if brace_match:
        return brace_match.group(1).strip()

    return stripped
