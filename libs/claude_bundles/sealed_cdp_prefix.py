"""Peel / parse leading sealed CDP skill prefixes (slash + authority + inline).

Owns text-idempotent normalization so admit→worker re-stage cannot stack a
second slash manifest into the pasted body.
"""

from __future__ import annotations

import re

_LEADING_SLASH_SKILL = re.compile(r"^(/[\w-]+)\r?\n")
_INLINE_SKILL_SLUG = re.compile(r'<skill slug="([^"]+)"')
_LEADING_AUTHORITY = re.compile(
    r"^(?:\r?\n)*<!--cdp-required-skills:([^\n]*?)-->\r?\n?"
)
_LEADING_INLINE_BLOCK = re.compile(
    r"^(?:\r?\n)*<skills_inline>.*?</skills_inline>(?:\r?\n)*",
    re.DOTALL,
)


def split_leading_slash_skills(text: str) -> tuple[list[str], str]:
    """Parse consecutive leading ``/<slug>\\n`` lines for composer chip bind.

    Consumes exactly one line break per slash line so trailing blank lines before
    body prose remain in ``rest`` for ``insert_text`` replay.
    """
    tokens: list[str] = []
    rest = text
    while True:
        match = _LEADING_SLASH_SKILL.match(rest)
        if match is None:
            break
        tokens.append(match.group(1))
        rest = rest[match.end() :]
    return tokens, rest


def extract_inline_slugs_from_sealed(rest: str) -> list[str]:
    """Return slug names from a staged ``<skills_inline>`` XML block in ``rest``.

    Pin: only ``<skill slug=\"…\">`` attributes match. A rename to ``name=``
    yields no inline slug — required authority must still come from
    ``<!--cdp-required-skills:…-->``, not this parse.
    """
    if "<skills_inline>" not in rest:
        return []
    return _INLINE_SKILL_SLUG.findall(rest)


def peel_sealed_cdp_skill_prefix(
    text: str,
) -> tuple[list[str], list[str], str]:
    """Strip all leading sealed skill prefixes; return ``(attach, inline, body)``.

    Peels repeated slash / authority / ``<skills_inline>`` blocks so
    ``prepend_cdp_dispatch_skills`` is text-idempotent under
    ``stage(stage(x))`` (admit then worker re-stage on the same prompt.md).
    """
    attach: list[str] = []
    inline: list[str] = []
    rest = text
    seen_attach: set[str] = set()
    seen_inline: set[str] = set()
    for _ in range(32):  # hard cap — sealed prefixes are tiny
        auth = _LEADING_AUTHORITY.match(rest)
        if auth is not None:
            rest = rest[auth.end() :]
            continue
        tokens, after = split_leading_slash_skills(rest)
        if tokens:
            for token in tokens:
                slug = token.removeprefix("/").strip()
                key = slug.lower()
                if slug and key not in seen_attach:
                    seen_attach.add(key)
                    attach.append(slug)
            rest = after
            continue
        inline_match = _LEADING_INLINE_BLOCK.match(rest)
        if inline_match is not None:
            block = inline_match.group(0)
            for slug in extract_inline_slugs_from_sealed(block):
                key = slug.lower()
                if key not in seen_inline:
                    seen_inline.add(key)
                    inline.append(slug)
            rest = rest[inline_match.end() :]
            continue
        break
    return attach, inline, rest
