"""Cowork/CDP skill delivery — local inject, not GitHub paths (friction 24594).

Skill trees under ``.cursor/`` and ``.*`` are gitignored. Listing a checkout
``SKILL.md`` path in a prompt does **not** load the skill via the GitHub
connector. Customize → Skills only carries ``shared_sync`` ∪ ``life_local``.

Roleless ``team_dispatch(model=cdp/…)`` skills= delivery (fleet rule):
- One ``shared_sync`` slug → single ``/<slug>\\n`` chip line
- Two or more ``shared_sync`` slugs → hybrid: ``/<first>\\n`` chip + ``Use the … skill``
  lines for the rest (consecutive multi-slash silently misses skills — friction 5588/5590)
- Not a Claude slug → inline SOT bodies at top of sealed prompt
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from claude_bundles.catalog import get_skill_catalog

DeliveryChannel = Literal["inject", "customize_skills", "unavailable"]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEADING_SLASH_SKILL = re.compile(r"^(/[\w-]+)(?:\r?\n)+")

# Flip only after ≥2 green harvests across Chat and Cowork with archive URIs.
MULTI_CHIP_PROVEN: bool = False


@dataclass(frozen=True, slots=True)
class SkillDeliveryPlan:
    """Per-slug delivery classification for a Cowork/CDP consult."""

    slug: str
    surface_class: str
    channel: DeliveryChannel
    sot_path: Path | None = None


@dataclass(frozen=True, slots=True)
class InjectedSkillBody:
    """Local SOT body ready to prepend into a Cowork prompt."""

    slug: str
    surface_class: str
    path: Path
    body: str


class SkillDeliveryError(RuntimeError):
    """Fail-closed when a consult claims skills that were not delivered."""


def is_claude_slug(slug: str) -> bool:
    """True when ``slug`` is a Customize→Skills / shared_sync Claude slug."""
    catalog = get_skill_catalog()
    return catalog.get(slug).surface_class == "shared_sync"


def classify_skill_delivery(slug: str) -> SkillDeliveryPlan:
    """Map catalog surface → lawful Cowork delivery channel.

    - ``cursor_only`` → local inject only (never GitHub, never Customize)
    - ``shared_sync`` / ``life_local`` → Customize Skills **or** inject
    """
    catalog = get_skill_catalog()
    entry = catalog.get(slug)
    channel: DeliveryChannel
    if entry.surface_class == "cursor_only":
        channel = "inject"
    else:
        channel = "customize_skills"
    sot: Path | None = None
    try:
        sot, _ = catalog.resolve_sot(entry.slug, _REPO_ROOT)
    except FileNotFoundError:
        sot = None
    return SkillDeliveryPlan(
        slug=entry.slug,
        surface_class=entry.surface_class,
        channel=channel,
        sot_path=sot,
    )


def partition_cdp_skills(slugs: list[str]) -> tuple[list[str], list[str]]:
    """Split caller skills into (claude slash slugs, inline slugs).

    Raises ``SkillDeliveryError`` / ``KeyError`` when a slug is not catalogued.
    """
    catalog = get_skill_catalog()
    slash: list[str] = []
    inline: list[str] = []
    seen: set[str] = set()
    for raw in slugs:
        entry = catalog.get(raw)
        if entry.slug in seen:
            continue
        seen.add(entry.slug)
        if entry.surface_class == "shared_sync":
            slash.append(entry.slug)
        else:
            inline.append(entry.slug)
    return slash, inline


def format_cdp_use_the_lines(slugs: list[str]) -> str:
    """Render ``Use the `{slug}` skill\\n`` lines for trailing shared_sync slugs.

    Cowork/Chat composer binds only the first leading ``/<slug>\\n`` as a
    ``<command-name>`` chip. Additional shared_sync slugs must use the
    Customize ``Use the … skill`` idiom so each body loads — not consecutive
    slash lines (dogfood 5588/5590 silent miss).

    Contract: ``slugs`` is the *remaining* shared_sync list after the chip
    slug; empty → ``""``; order preserved; one line per slug.
    """
    if not slugs:
        return ""
    return "".join(f"Use the `{slug}` skill\n" for slug in slugs)


def format_cdp_hybrid_prefix(slugs: list[str]) -> str:
    """Render hybrid CDP prefix for ordered ``shared_sync`` slugs.

    Why: only ``slug[0]`` reliably chip-binds; ``|slugs|≥2`` consecutive
    ``/<slug>\\n`` lines glue later slugs into the first skill's args.

    Contract:
    - ``len==0`` → ``""``
    - ``len==1`` → ``/{slug}\\n`` (pure chip — unchanged N=1 path)
    - ``len≥2`` → ``/{first}\\n`` + ``Use the `{rest}` skill\\n`` per remainder
    """
    if not slugs:
        return ""
    if len(slugs) == 1:
        return f"/{slugs[0]}\n"
    return f"/{slugs[0]}\n" + format_cdp_use_the_lines(slugs[1:])


def format_cdp_slash_prefix(
    slugs: list[str],
    *,
    allow_proven_multi_chip: bool = False,
) -> str:
    """Render consecutive ``/<slug>\\n`` lines — **single-slug or proven multi only**.

    Fail-closed at the lowest slash emitter: ``|slugs|≥2`` without an explicit
    ``allow_proven_multi_chip`` (or ``MULTI_CHIP_PROVEN``) raises
    ``SkillDeliveryError`` — consecutive multi-slash silently drops skills
    (friction 5588/5590). Callers delivering ``|shared_sync|≥2`` must use
    ``format_cdp_hybrid_prefix`` instead.
    """
    if not slugs:
        return ""
    if len(slugs) >= 2 and not (allow_proven_multi_chip or MULTI_CHIP_PROVEN):
        raise SkillDeliveryError(
            f"refusing consecutive multi-slash for {len(slugs)} shared_sync slugs "
            f"{slugs!r} — only first chip binds; use format_cdp_hybrid_prefix "
            "(friction 5588/5590)"
        )
    return "".join(f"/{slug}\n" for slug in slugs)


def split_leading_slash_skills(text: str) -> tuple[list[str], str]:
    """Parse consecutive leading ``/<slug>\\n`` lines for composer chip bind."""
    tokens: list[str] = []
    rest = text
    while True:
        match = _LEADING_SLASH_SKILL.match(rest)
        if match is None:
            break
        tokens.append(match.group(1))
        rest = rest[match.end() :]
    return tokens, rest


def render_cdp_inline_skills_xml(bodies: list[InjectedSkillBody]) -> str:
    """XML-delimited inline skills for roleless CDP (team_dispatch packet idiom).

    Claude slugs stay as leading ``/<slug>\\n`` lines — not XML. Only non-Claude
    bodies use this wrapper so the sealed prompt stays parseable and distinct
    from Customize chip binds.
    """
    if not bodies:
        return ""
    parts = [
        "<skills_inline>",
        "<!-- Local SOT bodies — NOT GitHub; ¬ slash these slugs -->",
    ]
    for item in bodies:
        parts.append(
            f'<skill slug="{item.slug}" surface_class="{item.surface_class}">'
        )
        parts.append(item.body.rstrip())
        parts.append("</skill>")
    parts.append("</skills_inline>")
    parts.append("")
    return "\n".join(parts) + "\n"


def prepend_cdp_dispatch_skills(
    prompt: str,
    slugs: list[str] | None,
    *,
    repo_root: Path | None = None,
) -> tuple[str, list[str], list[InjectedSkillBody]]:
    """Prepend CDP skills= delivery to a sealed prompt.

    ``shared_sync`` slugs use hybrid delivery (``|slash|==1`` → chip;
    ``|slash|≥2`` → first chip + ``Use the … skill`` for rest). All other
    catalog skills are inlined inside ``<skills_inline>`` XML with a blank
    line separator when both blocks are present.
    """
    if not slugs:
        return prompt, [], []
    slash_slugs, inline_slugs = partition_cdp_skills(list(slugs))
    slash_block = format_cdp_hybrid_prefix(slash_slugs)
    bodies: list[InjectedSkillBody] = []
    inline_block = ""
    if inline_slugs:
        bodies = load_skill_bodies(inline_slugs, repo_root=repo_root)
        inline_block = render_cdp_inline_skills_xml(bodies)
    if slash_block and inline_block:
        prefix = f"{slash_block}\n{inline_block}"
    else:
        prefix = f"{slash_block}{inline_block}"
    if not prefix:
        return prompt, slash_slugs, bodies
    # Blank line between slash chip lines and body when no XML inline follows.
    if slash_block and not inline_block and not prompt.startswith("\n"):
        prefix = f"{slash_block}\n"
    return f"{prefix}{prompt}", slash_slugs, bodies


def plan_skill_delivery(slugs: list[str]) -> list[SkillDeliveryPlan]:
    """Classify each required slug (canonicalized via catalog)."""
    return [classify_skill_delivery(s) for s in slugs]


def load_skill_bodies(
    slugs: list[str],
    *,
    repo_root: Path | None = None,
) -> list[InjectedSkillBody]:
    """Read local SOT bodies for inject. Raises if any slug has no SOT file."""
    root = repo_root or _REPO_ROOT
    catalog = get_skill_catalog()
    out: list[InjectedSkillBody] = []
    missing: list[str] = []
    for raw in slugs:
        entry = catalog.get(raw)
        try:
            path, _ = catalog.resolve_sot(entry.slug, root)
        except FileNotFoundError:
            missing.append(entry.slug)
            continue
        out.append(
            InjectedSkillBody(
                slug=entry.slug,
                surface_class=entry.surface_class,
                path=path,
                body=path.read_text(encoding="utf-8"),
            )
        )
    if missing:
        raise SkillDeliveryError(
            f"no local SOT for inject: {missing} — cannot claim skills loaded"
        )
    return out


def render_injected_skills_block(bodies: list[InjectedSkillBody]) -> str:
    """Markdown block to prepend — explicit bodies, not path pointers."""
    if not bodies:
        return ""
    parts = [
        "# Injected skill bodies (local seat — NOT GitHub)",
        "",
        "These bodies were read from the local checkout by the dispatching seat.",
        "Skill trees are gitignored (`.cursor/`, `.*`) — the GitHub connector",
        "cannot load them. Do not treat path citations as loaded skills.",
        "",
    ]
    for item in bodies:
        parts.append(f"## skill:{item.slug} ({item.surface_class})")
        parts.append("")
        parts.append(item.body.rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n\n"


def prepend_injected_skills(
    prompt: str,
    slugs: list[str],
    *,
    repo_root: Path | None = None,
) -> tuple[str, list[InjectedSkillBody]]:
    """Return ``injected_block + prompt`` and the bodies used."""
    bodies = load_skill_bodies(slugs, repo_root=repo_root)
    block = render_injected_skills_block(bodies)
    return f"{block}{prompt}", bodies


def attest_skills_chip_enabled(
    enabled: list[str] | None,
    *,
    required: list[str],
) -> list[str]:
    """Fail closed when the runner claims Customize/Skills-chip attach.

    ``enabled`` = ledger ``skills.enabled``. Empty chip + non-empty ``required``
    ⇒ abort before treating the consult as skill-backed (friction 24594).
    """
    if not required:
        return list(enabled or [])
    got = [str(s).strip() for s in (enabled or []) if s and str(s).strip()]
    if not got:
        raise SkillDeliveryError(
            "skills.enabled is empty but runner claimed Skills-chip attach "
            f"(required={required}) — fail closed (friction 24594)"
        )
    return got


def attest_injected_slugs(
    injected: list[str] | None,
    *,
    required: list[str],
) -> list[str]:
    """Fail closed when required bodies were not injected into the prompt."""
    if not required:
        return list(injected or [])
    catalog = get_skill_catalog()
    got = {catalog.canonical_slug(s) for s in (injected or []) if s}
    missing = [
        catalog.canonical_slug(s) for s in required if catalog.canonical_slug(s) not in got
    ]
    if missing:
        raise SkillDeliveryError(
            f"injected skills missing required={missing} — fail closed (friction 24594)"
        )
    return sorted(got)


def github_cannot_load_skill_trees_note() -> str:
    """One-liner for prompts / ledgers — retract the false GitHub-load claim."""
    return (
        "¬ load `.cursor/skills/**` or `.claude/skills/**` via GitHub — "
        "those trees are gitignored; inject locally or use Customize Skills "
        "for shared_sync/life_local only."
    )


def ledger_skills_record(
    *,
    enabled: list[str],
    injected: list[str],
    channel: str = "inject",
) -> dict[str, Any]:
    """Ledger shape for CDP runners — ok iff delivery succeeded."""
    delivered = sorted(set(enabled) | set(injected))
    return {
        "ok": bool(delivered),
        "enabled": list(enabled),
        "injected": list(injected),
        "channel": channel,
        "note": github_cannot_load_skill_trees_note(),
    }
