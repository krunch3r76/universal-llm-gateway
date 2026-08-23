"""Visible CDP read cue for non-attach (``cursor_only``) skill inject.

Life Customize Skills does not carry ``cursor_only`` slugs. ``<skills_inline>``
excerpts are the delivered body; this module tells the seat to read them and
gives an ``fs`` SOT path when the excerpt is truncated. Paths come from
``catalog.resolve_sot``, never a hub-only URI guess. Census SOTs live under
the plugin tree.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

WORKSPACES_REPO_PREFIX = "universal-llm-gateway"
READ_BLOCK_HEADING = "## cursor_only skills — not on this seat's Skill loader"

_SKILL_POINTER_LINE_RE = re.compile(
    r"(?:-\s+)?Use the `(?P<slug>[a-z0-9][-a-z0-9_]*)` skill"
    r"(?: \([^<\n]*\))?",
    re.IGNORECASE,
)


def workspaces_fs_skill_path(sot: Path, repo_root: Path) -> str:
    """Checkout-relative workspaces ``path=`` from a ``resolve_sot`` file."""
    rel = sot.resolve().relative_to(repo_root.resolve())
    return f"{WORKSPACES_REPO_PREFIX}/{rel.as_posix()}"


def emit_workspaces_fs_read(sot: Path, repo_root: Path) -> str:
    """Life MCP ``fs`` read of the full SOT (truncated-excerpt fallback)."""
    path = workspaces_fs_skill_path(sot, repo_root)
    return f'fs(sandbox="workspaces", op="read", path="{path}")'


def render_cdp_inline_read_block(
    items: Sequence[tuple[str, str, Path]],
    *,
    repo_root: Path,
) -> str:
    """Visible prose: not on Skill loader; read excerpt; ``fs`` if truncated."""
    if not items:
        return ""
    lines = [
        READ_BLOCK_HEADING,
        "",
        "These slugs are not on this seat's Skill loader (life segregation).",
        "Do not /slash them or wait on + → Skills. Read the <skill> excerpt below.",
        "Do not emit `Use the {slug} skill` for these slugs — that verb is",
        "Customize self-fetch and will miss.",
        "",
    ]
    for slug, surface, path in items:
        fs_line = emit_workspaces_fs_read(path, repo_root)
        lines.append(
            f"- `{slug}` (`{surface}`) — read the excerpt below. If truncated:"
        )
        lines.append(f"  {fs_line}")
    lines.append("")
    return "\n".join(lines)


def rewrite_inline_use_the_lines(text: str, inline_slugs: set[str]) -> str:
    """Replace Use-the/self-fetch lines for inlined slugs with a read cue."""
    if not inline_slugs:
        return text
    lowered = {slug.lower() for slug in inline_slugs}

    def _replace(match: re.Match[str]) -> str:
        slug = match.group("slug")
        if slug.lower() not in lowered:
            return match.group(0)
        return (
            f"- Read the inlined `{slug}` excerpt in <skills_inline> "
            "(not on this seat's Skill loader; if truncated, "
            "fs-read the SOT in the read block)"
        )

    return _SKILL_POINTER_LINE_RE.sub(_replace, text)
