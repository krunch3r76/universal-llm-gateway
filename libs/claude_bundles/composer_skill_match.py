"""Cowork ``+`` → Skills picker matching (H1 titles, not the Customize table).

Three label surfaces (do not conflate): Customize Skills **table** and post-attach
chips/context rail show the kebab **slug**; the Cowork attach **picker** shows the
uploaded SKILL.md **H1**. Start-anchored hyphen-to-space regexes missed mixed
H1s (friction a:30502 — ``Life operator do-chain`` vs
``life-operator-do-chain``). Collapse separators, then compare; keep
start-anchor so a suffix superstring cannot steal the click.

Uploaded Customize bytes are normalized (``normalize_first_h1``) so a literary
SOT title cannot ship a picker H1 that fails this match.
"""

from __future__ import annotations

import re

_PUA_RE = re.compile(r"[\ue000-\uf8ff]")
_SEP_RE = re.compile(r"[-_/\s→—–]+")
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n?", re.DOTALL)


def strip_pua(label: str) -> str:
    """Drop Private Use Area glyphs (Cowork menu chevrons) from a label."""
    return _PUA_RE.sub("", label).strip()


def collapse_separators(text: str) -> str:
    """Fold hyphen/underscore/slash/dash/arrow/space to one space; casefold."""
    return _SEP_RE.sub(" ", strip_pua(text)).strip().casefold()


def label_matches_slug(slug: str, label: str) -> bool:
    """True when a Cowork Skills-list label names this slug.

    Collapsed haystack equals the collapsed slug, or starts with
    ``{slug} `` so subtitles stay legal (``CDP Operator Proxy — …``).
    Suffix superstrings (``meta-reasoning-posture-draft`` vs
    ``reasoning-posture``) do not match.
    """
    needle = collapse_separators(slug)
    hay = collapse_separators(label)
    if not needle or not hay:
        return False
    return hay == needle or hay.startswith(f"{needle} ")


def title_from_slug(slug: str) -> str:
    """Humanize a kebab slug into an attach-safe H1 prefix.

    Collapse-equal to the slug after ``label_matches_slug`` (first token
    capitalized, rest lower, hyphens to spaces).
    """
    tokens = [part for part in re.split(r"[-_]+", slug.strip()) if part]
    if not tokens:
        return slug.strip()
    head, *tail = tokens
    return " ".join(
        [head[:1].upper() + head[1:].lower(), *[part.lower() for part in tail]]
    )


def first_h1(text: str) -> str | None:
    """Return the first markdown H1 title in a SKILL.md or body."""
    body = _FRONTMATTER_RE.sub("", text, count=1) if text.startswith("---") else text
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        return None
    return None


def normalize_first_h1(slug: str, body: str) -> tuple[str, bool]:
    """Rewrite the first H1 so Cowork attach can match *slug*.

    Leave a reachable H1 alone. Otherwise prefix ``title_from_slug`` and keep
    the literary title as a subtitle (``Life to code request lane — Life→Code``).
    Insert a prefix H1 when the body has no heading.
    """
    if not collapse_separators(slug):
        return body, False
    prefix = title_from_slug(slug)
    lines = body.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if not stripped.startswith("#"):
            insert = f"# {prefix}\n\n"
            return insert + body, True
        title = stripped.lstrip("#").strip()
        if label_matches_slug(slug, title):
            return body, False
        ending = "\n" if line.endswith("\n") else ""
        lines[index] = f"# {prefix} — {title}{ending}"
        return "".join(lines), True
    insert = f"# {prefix}\n\n" if body else f"# {prefix}\n"
    return insert + body, True


def attach_h1_error(slug: str, skill_md: str) -> str | None:
    """Return a check error when the H1 is not attach-reachable."""
    title = first_h1(skill_md)
    if title is None:
        return f"ATTACH-H1: {slug} has no H1"
    if label_matches_slug(slug, title):
        return None
    return f"ATTACH-H1: {slug} H1 {title!r} is not label_matches_slug"
