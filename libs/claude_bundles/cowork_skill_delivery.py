"""Cowork/CDP skill delivery — local inject, not GitHub paths (friction 24594).

Skill trees under ``.cursor/`` and ``.*`` are gitignored. Listing a checkout
``SKILL.md`` path in a prompt does **not** load the skill via the GitHub
connector. Customize → Skills only carries ``shared_sync`` ∪ ``life_local``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from claude_bundles.catalog import get_skill_catalog

DeliveryChannel = Literal["inject", "customize_skills", "unavailable"]

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
