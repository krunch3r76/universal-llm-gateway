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
_LEADING_SKILL_HASH = re.compile(
    r"^Hash these skills from your local skill server and quote each digest before you answer:.*\n"
)
REVIEW_READING_CHARTER = (
    "Review seat: the packet carries the code under review. "
    "Read that, and browse the live tree for other code. "
    "Do not REJECT because you cannot check out a commit, run pytest, or run quality_gate. "
    "A conflict between this live tree and land is reconciled later by a reasoning seat.\n"
)
_LEADING_REVIEW_CHARTER = re.compile(
    r"^Review seat: the packet carries the code under review\..*\n"
)
_LEADING_INLINE_BLOCK = re.compile(
    r"^(?:\r?\n)*<skills_inline>.*?</skills_inline>(?:\r?\n)*",
    re.DOTALL,
)
_USE_LINE_DELIVERY_RE = re.compile(
    r"(?:-\s+)?Use the `?(?P<slug>[a-z0-9][-a-z0-9_]*)`? skill"
    r"(?: \([^<\n]*\))?",
    re.IGNORECASE,
)
_HOUSE_READ_FIRST_HEADING = "## House (read first)"
_HOP_SUCCESSOR_HEADING = "# Hop on agent-bus:"


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


def _peel_house_read_first_block(rest: str) -> tuple[bool, str]:
    """Consume a staged ``## House (read first)`` block when briefing follows."""
    if not rest.lstrip("\r\n").startswith(_HOUSE_READ_FIRST_HEADING):
        return False, rest
    hop_marker = f"\n\n{_HOP_SUCCESSOR_HEADING}"
    hop_idx = rest.find(hop_marker)
    if hop_idx == -1:
        return False, rest
    return True, rest[hop_idx + 2 :]


def peel_delivery_prefix(text: str) -> str:
    """Strip leading delivery chrome for idempotency detection.

    Peels authority, slash lines, inline XML, skill hash, review charter,
    Use-the lines (backticks optional), and the House read-first block when
    the hop briefing follows. Does not alter ``peel_sealed_cdp_skill_prefix``
    attach/inline accounting.
    """
    rest = text
    for _ in range(128):
        if rest.startswith("\r\n"):
            rest = rest[2:]
            continue
        if rest.startswith("\n"):
            rest = rest[1:]
            continue
        auth = _LEADING_AUTHORITY.match(rest)
        if auth is not None:
            rest = rest[auth.end() :]
            continue
        tokens, after = split_leading_slash_skills(rest)
        if tokens:
            rest = after
            continue
        skill_hash = _LEADING_SKILL_HASH.match(rest)
        if skill_hash is not None:
            rest = rest[skill_hash.end() :]
            continue
        charter = _LEADING_REVIEW_CHARTER.match(rest)
        if charter is not None:
            rest = rest[charter.end() :]
            continue
        inline_match = _LEADING_INLINE_BLOCK.match(rest)
        if inline_match is not None:
            rest = rest[inline_match.end() :]
            continue
        peeled_house, after_house = _peel_house_read_first_block(rest)
        if peeled_house:
            rest = after_house
            continue
        line_end = rest.find("\n")
        first = rest if line_end == -1 else rest[:line_end]
        use_match = _USE_LINE_DELIVERY_RE.match(first)
        if use_match is not None and use_match.group(0) == first.rstrip("\r"):
            rest = rest[len(first) :]
            if rest.startswith("\r\n"):
                rest = rest[2:]
            elif rest.startswith("\n"):
                rest = rest[1:]
            continue
        break
    return rest


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
        skill_hash = _LEADING_SKILL_HASH.match(rest)
        if skill_hash is not None:
            rest = rest[skill_hash.end() :]
            continue
        charter = _LEADING_REVIEW_CHARTER.match(rest)
        if charter is not None:
            rest = rest[charter.end() :]
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


def ensure_review_reading_charter(
    text: str,
    purpose: str | None,
    *,
    author_body: str | None = None,
) -> str:
    """Place the reading-review line after the skill hash. Idempotent under peel.

    ``job=delivery-review`` delivery/code-review only. The packet carries the code.
    CDP reads the live tree. Checkout, pytest, and quality_gate are not this
    seat's reject grounds.

    a:37183 — never inject this chrome onto G4 / SKEPTIC / adversarial-spec
    *author* bodies; those are spec-skeptic seats, not delivery review.
    Shape detection uses ``author_body`` when provided (A1 — pre-skill text).
    """
    if (purpose or "").strip().lower() != "review":
        return text
    from claude_bundles.nested_cdp_prompt_gate import is_skeptic_shaped

    shape_src = author_body if author_body is not None else text
    if is_skeptic_shaped(shape_src):
        return text
    if "the packet carries the code under review" in text.casefold():
        return text
    marker = "Hash these skills from your local skill server"
    idx = text.find(marker)
    if idx == -1:
        return f"{REVIEW_READING_CHARTER}{text}"
    end = text.find("\n", idx)
    if end == -1:
        return f"{text}\n{REVIEW_READING_CHARTER}"
    return f"{text[: end + 1]}{REVIEW_READING_CHARTER}{text[end + 1 :]}"
