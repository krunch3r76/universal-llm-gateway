"""Cowork/CDP skill delivery — local inject, not GitHub paths (friction 24594).

Skill trees under ``.cursor/`` and ``.*`` are gitignored. Listing a checkout
``SKILL.md`` path in a prompt does **not** load the skill via the GitHub
connector. Customize → Skills only carries ``shared_sync`` ∪ ``life_local``.

Roleless ``team_dispatch(model=cdp/…)`` skills= delivery (fleet rule;
operator bind 2026-07-26 — multi-skill via composer **+ → Skills → pick**):
- Staging always merges ``reasoning-posture`` (``decision:reasoning-frontier-skill-pair``),
  including light-bounded / omitted ``skills=``
- ``shared_sync`` slugs → leading ``/<slug>\\n`` **manifest** lines (not typed);
  ``project_ask`` / ``send_prompt`` attaches each via + → Skills → list select
- Non-Claude / ``cursor_only`` → **read-instructed** ``<skills_inline>`` XML
  **excerpts** (size-gated — full SKILL.md never sealed; friction a:27142).
  Visible read cue: not on life Skill loader; read the excerpt; ``fs`` SOT
  if truncated. Use-the/self-fetch lines for those slugs are rewritten.
- Hybrid (``/<first>\\n`` + ``Use the … skill``) is **escape only** when live
  chip-glue is observed (friction 5588/5590) — not the default
- Slash-type multi-chip is **retired** (a25806 — only first `/slug` binds)
- Manual Cowork composer paste does **not** chip-bind; automation path only
- Light-bounded architect / admit binds: judgment skill (+ consult-posture);
  ¬ ``path-sim`` in CDP ``skills=`` (enforced at ``stage_cdp_prompt_with_skills``,
  a:27430). Cascade Q/R legs seal path-sim into the prompt URI — they do not
  list the slug in ``skills=``. ``partition_cdp_skills`` remains channel-only.

Entry points that must hit attach (not type):
``team_dispatch(model=cdp/…)`` staging → satellite ``run_project_ask`` /
``send_prompt``; MCP ``project_ask``; converse follow-ups; CLI
``cowork_project_ask``. Shared runtime: ``attach_session_skills``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from claude_bundles.catalog import get_skill_catalog
from claude_bundles.cdp_inline_read_cue import (
    render_cdp_inline_read_block,
    rewrite_inline_use_the_lines,
)
from claude_bundles.events_skill_delivery import emit_skill_delivery_attested
from claude_bundles.sealed_cdp_prefix import (
    extract_inline_slugs_from_sealed,
    peel_sealed_cdp_skill_prefix,
    split_leading_slash_skills,
)

DeliveryChannel = Literal["inject", "customize_skills", "unavailable"]

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Required authority sealed at staging — NOT derived from attach/inline channels
# (amended A4: required must survive XML drop/rename; R-after decisive_falsifier).
_CDP_REQUIRED_AUTHORITY = re.compile(r"<!--cdp-required-skills:([^\n]*?)-->\r?\n?")

# Operator 2026-07-26: multi-skill attach = composer + → Skills → pick-each.
# Flag name is historical (pre-attach era); True means multi-shared_sync
# slash *manifest* lines are lawful — runtime still attaches via + → Skills.
# Slash-type is retired (a25806).
MULTI_CHIP_PROVEN: bool = True


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
    """Sole CDP ``skills=`` disposition authority — ``surface_class`` → channel.

    ``shared_sync`` slugs route to the Customize attach manifest (leading
    ``/<slug>\\n`` lines). All other catalog surface classes (e.g.
    ``cursor_only`` such as ``path-sim``) route to ``<skills_inline>`` XML.
    ``mcp_surface_required`` is **not** consulted here — code-MCP classification
    does not reject or reroute CDP delivery.

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
    """Render ``Use the `{slug}` skill\\n`` lines — hybrid **escape** remainder.

    Default CDP delivery is consecutive ``/<slug>\\n`` (``format_cdp_slash_prefix``).
    Use this only when composing ``format_cdp_hybrid_prefix`` after live chip-glue
    (friction 5588/5590).

    Contract: ``slugs`` is the *remaining* shared_sync list after the chip
    slug; empty → ``""``; order preserved; one line per slug.
    """
    if not slugs:
        return ""
    return "".join(f"Use the `{slug}` skill\n" for slug in slugs)


def format_cdp_hybrid_prefix(slugs: list[str]) -> str:
    """Escape-path CDP prefix when multi-slash chip-glue is observed.

    Default path is ``format_cdp_slash_prefix`` (operator bind 2026-07-26).
    Keep this helper for recovery: first chip + ``Use the … skill`` remainder
    (friction 5588/5590).

    Contract:
    - ``len==0`` → ``""``
    - ``len==1`` → ``/{slug}\\n``
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
    """Render consecutive ``/<slug>\\n`` lines — CDP canonical skill prefix.

    Multi-slash is the default when ``MULTI_CHIP_PROVEN`` (operator-confirmed
    2026-07-26). Explicit ``allow_proven_multi_chip=False`` with the flag off
    still fail-closes so callers can force hybrid escape via
    ``format_cdp_hybrid_prefix``.
    """
    if not slugs:
        return ""
    if len(slugs) >= 2 and not (allow_proven_multi_chip or MULTI_CHIP_PROVEN):
        raise SkillDeliveryError(
            f"refusing consecutive multi-slash for {len(slugs)} shared_sync slugs "
            f"{slugs!r} — MULTI_CHIP_PROVEN is False; use format_cdp_hybrid_prefix "
            "(friction 5588/5590 escape) or set allow_proven_multi_chip=True"
        )
    return "".join(f"/{slug}\n" for slug in slugs)


def render_cdp_inline_skills_xml(
    bodies: list[InjectedSkillBody],
    *,
    repo_root: Path | None = None,
) -> str:
    """XML-delimited inline skills for roleless CDP (team_dispatch packet idiom).

    Claude slugs stay as leading ``/<slug>\\n`` lines — not XML. Only non-Claude
    bodies use this wrapper so the sealed prompt stays parseable and distinct
    from Customize chip binds. A visible read cue precedes the skill tags
    (not on Skill loader; read excerpt; ``fs`` if truncated).
    """
    if not bodies:
        return ""
    root = repo_root or _REPO_ROOT
    read_block = render_cdp_inline_read_block(
        [(item.slug, item.surface_class, item.path) for item in bodies],
        repo_root=root,
    )
    parts = [
        "<skills_inline>",
        read_block.rstrip(),
        "<!-- Local SOT bodies — NOT GitHub; ¬ slash these slugs -->",
    ]
    for item in bodies:
        parts.append(f'<skill slug="{item.slug}" surface_class="{item.surface_class}">')
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
    hybrid_escape: bool = False,
) -> tuple[str, list[str], list[InjectedSkillBody]]:
    """Prepend CDP skills= delivery to a sealed prompt.

    ``shared_sync`` slugs default to consecutive ``/<slug>\\n`` chip lines
    (``format_cdp_slash_prefix``). Pass ``hybrid_escape=True`` only when live
    chip-glue forces the friction 5588/5590 escape. All other catalog skills
    are inlined inside ``<skills_inline>`` XML (read-instructed) with a blank
    line separator when both blocks are present. Use-the/self-fetch lines
    for inlined slugs in the remaining body are rewritten to a read cue.

    Text-idempotent: peels any existing leading sealed skill prefix before
    rebuilding, so ``stage(stage(x))`` yields a single manifest block.
    """
    if not slugs:
        return prompt, [], []
    requested = [str(s).strip() for s in slugs if str(s).strip()]
    root = repo_root or _REPO_ROOT
    _peeled_attach, _peeled_inline, body = peel_sealed_cdp_skill_prefix(prompt)
    slash_slugs, inline_slugs = partition_cdp_skills(list(slugs))
    slash_block = (
        format_cdp_hybrid_prefix(slash_slugs)
        if hybrid_escape
        else format_cdp_slash_prefix(slash_slugs)
    )
    bodies: list[InjectedSkillBody] = []
    inline_block = ""
    if inline_slugs:
        bodies = load_skill_bodies(inline_slugs, repo_root=root, excerpt=True)
        inline_block = render_cdp_inline_skills_xml(bodies, repo_root=root)
        body = rewrite_inline_use_the_lines(body, set(inline_slugs))
    if slash_block and inline_block:
        prefix = f"{slash_block}\n{inline_block}"
    else:
        prefix = f"{slash_block}{inline_block}"
    authority = render_cdp_required_authority(requested)
    if not prefix:
        return f"{authority}{body}", slash_slugs, bodies
    # Blank line between slash chip lines and body when no XML inline follows.
    if slash_block and not inline_block and not body.startswith("\n"):
        prefix = f"{slash_block}\n"
    return f"{prefix}{authority}{body}", slash_slugs, bodies


def plan_skill_delivery(slugs: list[str]) -> list[SkillDeliveryPlan]:
    """Classify each required slug (canonicalized via catalog)."""
    return [classify_skill_delivery(s) for s in slugs]


def load_skill_bodies(
    slugs: list[str],
    *,
    repo_root: Path | None = None,
    excerpt: bool = False,
) -> list[InjectedSkillBody]:
    """Read local SOT bodies for inject. Raises if any slug has no SOT file.

    Pass ``excerpt=True`` for CDP ``<skills_inline>`` (size-gated; a:27142).
    Non-CDP markdown inject keeps full bodies (``excerpt=False`` default).
    """
    from claude_bundles.cdp_inline_excerpt import excerpt_skill_body

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
        raw_body = path.read_text(encoding="utf-8")
        body = excerpt_skill_body(raw_body, slug=entry.slug) if excerpt else raw_body
        out.append(
            InjectedSkillBody(
                slug=entry.slug,
                surface_class=entry.surface_class,
                path=path,
                body=body,
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


def render_cdp_required_authority(slugs: list[str]) -> str:
    """Seal ``skills=`` required authority outside attach/inline channels.

    Marker wire shape: ``<!--cdp-required-skills:slug1,slug2-->`` (comma-separated,
    no spaces). Authority must not be reconstructed solely from delivery channels
    — dropping ``<skills_inline>`` must not shrink ``required`` (A4 / R-after H1).
    """
    canon = [str(s).strip() for s in slugs if str(s).strip()]
    return f"<!--cdp-required-skills:{','.join(canon)}-->\n"


def extract_cdp_required_authority(text: str) -> list[str] | None:
    """Return sealed required slugs, or ``None`` when the marker is absent."""
    match = _CDP_REQUIRED_AUTHORITY.search(text)
    if match is None:
        return None
    raw = match.group(1).strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def strip_cdp_required_authority(text: str) -> str:
    """Remove the first required-authority marker from a sealed prompt."""
    return _CDP_REQUIRED_AUTHORITY.sub("", text, count=1)


def parse_cdp_sealed_skill_channels(
    text: str,
) -> tuple[list[str], list[str], str]:
    """Reconstruct attach vs inline *delivery channels* from a sealed CDP prompt.

    Combines the leading slash manifest (attach) with ``<skills_inline>`` tags
    (inline). Does **not** own ``required`` — callers must use
    ``extract_cdp_required_authority`` (staging ``skills=``) so attest cannot
    go skill-blind when a delivery channel is dropped from the seal.
    """
    cleaned = strip_cdp_required_authority(text)
    slash_tokens, rest = split_leading_slash_skills(cleaned)
    slugs_from_slash = [token.removeprefix("/") for token in slash_tokens]
    attach_slugs = [slug for slug in slugs_from_slash if is_claude_slug(slug)]
    inline_slugs = extract_inline_slugs_from_sealed(rest)
    return attach_slugs, inline_slugs, rest


def _attach_only_surface(surface_class: str) -> bool:
    """True when CDP delivery must be Customize attach (not ``<skills_inline>``)."""
    return surface_class in {"shared_sync", "life_local"}


def attest_delivery_channels(
    required: list[str],
    *,
    attached: list[str],
    inlined: list[str],
    execution_id: str = "",
    satellite_execution_id: str = "",
) -> list[str]:
    """Fail closed when a required slug lacks the correct delivery channel.

    Channel by ``surface_class`` (friction a:27142 / 26986 / 24594):
    - ``shared_sync`` / ``life_local`` → **must** be in ``attached``
      (``+`` → Skills). Inline alone / slash-manifest-only does not count.
    - All other surfaces → **must** be in ``inlined`` (``<skills_inline>``).

    Axes (do not conflate):
    - ``surface_class`` — CDP delivery channel via ``partition_cdp_skills``
    - ``mcp_surface_required`` — runtime MCP residency signal; **not** a CDP
      ``skills=`` reject predicate (friction a:26986 / QA-1).

    Side effect (G1 Option A / invariant 4): emits
    ``cdp.skill.delivery_attested`` on success **and** fail via
    ``ledger_skills_channels`` rows + ``attached``/``inlined``/``undelivered``
    — best-effort; never relaxes fail-closed raise semantics.

    Correlation keys (AC-CORR): ``execution_id`` is the **Stargate** seating
    id; ``satellite_execution_id`` is the cdp_ask admit id. Both keys are always
    emitted (may be ``\"\"`` for non-seating harness callers).
    """
    if not required:
        return []
    catalog = get_skill_catalog()
    attached_set = {str(s).strip() for s in attached if str(s).strip()}
    inlined_set = {str(s).strip() for s in inlined if str(s).strip()}
    missing: list[str] = []
    wrong_channel: list[str] = []
    for raw in required:
        entry = catalog.get(raw)
        slug = entry.slug
        if _attach_only_surface(entry.surface_class):
            if slug in attached_set:
                continue
            if slug in inlined_set:
                wrong_channel.append(slug)
            else:
                missing.append(slug)
        else:
            if slug in inlined_set:
                continue
            if slug in attached_set:
                wrong_channel.append(slug)
            else:
                missing.append(slug)
    attached_sorted = sorted(attached_set)
    inlined_sorted = sorted(inlined_set)
    rows = ledger_skills_channels(
        required, attached=attached_sorted, inlined=inlined_sorted
    )
    stargate_id = str(execution_id or "")
    sat_id = str(satellite_execution_id or "")
    if missing or wrong_channel:
        emit_skill_delivery_attested(
            ok=False,
            attached=attached_sorted,
            inlined=inlined_sorted,
            undelivered=list(missing),
            wrong_channel=list(wrong_channel),
            rows=rows,
            execution_id=stargate_id,
            satellite_execution_id=sat_id,
        )
        parts: list[str] = []
        if missing:
            parts.append(f"undelivered={missing}")
        if wrong_channel:
            parts.append(f"wrong_channel={wrong_channel}")
        raise SkillDeliveryError(
            "required skills fail channel attest after attach + inline seal: "
            f"{'; '.join(parts)} (attached={attached_sorted}, "
            f"inlined={inlined_sorted}) — fail closed (friction a:27142)"
        )
    emit_skill_delivery_attested(
        ok=True,
        attached=attached_sorted,
        inlined=inlined_sorted,
        undelivered=[],
        wrong_channel=[],
        rows=rows,
        execution_id=stargate_id,
        satellite_execution_id=sat_id,
    )
    return list(required)


def ledger_skills_channels(
    required: list[str],
    *,
    attached: list[str],
    inlined: list[str],
) -> list[dict[str, str]]:
    """Build one ledger row per requested slug with ``delivered_via`` channel.

    ``attach`` / ``inline`` only when the channel matches ``surface_class``;
    otherwise ``undelivered`` (incl. wrong-channel). Callers assert
    ``len(rows) == len(required)`` before treating a dispatch as skill-backed.
    """
    catalog = get_skill_catalog()
    attached_set = {str(s).strip() for s in attached if str(s).strip()}
    inlined_set = {str(s).strip() for s in inlined if str(s).strip()}
    rows: list[dict[str, str]] = []
    for raw in required:
        entry = catalog.get(raw)
        slug = entry.slug
        if _attach_only_surface(entry.surface_class):
            via = "attach" if slug in attached_set else "undelivered"
        else:
            via = "inline" if slug in inlined_set else "undelivered"
        rows.append({"slug": slug, "delivered_via": via})
    return rows


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
        catalog.canonical_slug(s)
        for s in required
        if catalog.canonical_slug(s) not in got
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
    required: list[str] | None = None,
    channel: str = "inject",
) -> dict[str, Any]:
    """Ledger shape for CDP runners — ok iff every required slug has a channel."""
    attached = list(enabled)
    inlined = list(injected)
    req = (
        list(required) if required is not None else sorted(set(attached) | set(inlined))
    )
    rows = ledger_skills_channels(req, attached=attached, inlined=inlined)
    delivered = [row["slug"] for row in rows if row["delivered_via"] != "undelivered"]
    return {
        "ok": bool(req) and len(delivered) == len(req),
        "enabled": list(enabled),
        "injected": list(injected),
        "rows": rows,
        "channel": channel,
        "note": github_cannot_load_skill_trees_note(),
    }
