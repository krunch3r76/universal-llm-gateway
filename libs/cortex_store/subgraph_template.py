"""Markdown template builder for subgraph rendering.

Internal helper module for :mod:`subgraph_renderer`. Implements the V1.1
spec markdown template — deterministic byte-stable output with light
escape for user-content headings and code fences.

Reference: cortex://notes/system/threads/cortex-subgraph-render-v1.1-dispatch.md
"""

from __future__ import annotations

from itertools import groupby
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .subgraph_renderer import RenderedEdge, RenderedEntity


_DIRECTION_ARROW = {"outbound": "\u2192", "inbound": "\u2190", "cross": "\u2194"}

_PROVENANCE_URI_PREFIXES = (
    "cortex://",
    "workspaces:",
    "transcript:",
    "agent-bus://",
    "agent-bus:",
    "https://",
    "http://",
    "file://",
    "files://",
    "files:",
)


def _is_uri_shaped(value: object) -> bool:
    """True when ``value`` is a non-empty string with URI shape.

    Accepts known scheme prefixes (cortex://, workspaces:, transcript:,
    agent-bus://, https://, file://, files://) and absolute paths
    starting with ``/``. Free-text evidence descriptions like
    ``"various sources"`` do NOT qualify.
    """
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/"):
        return True
    return any(value.startswith(prefix) for prefix in _PROVENANCE_URI_PREFIXES)


def _provenance_flag(assertion: dict[str, Any]) -> str:
    """Return the leading-space provenance flag for an assertion, or empty.

    Three rules in evaluation order (V1.5 spec, refined by assertion 10630):

    1. ``[verbatim-quote]`` for ``derivation_type='quotation'`` — the
       asserter cited the literal source text.
    2. ``[primary-source-backed]`` when ``evidence_uris`` contains at
       least one URI-shaped entry (scheme or absolute path).
    3. ``[derived]`` for ``derivation_type='inference'`` — agent
       synthesis that wants spot-checking.

    Observation-class derivation types (direct_observation,
    agent_observation, user_statement, commitment, stated, other) carry
    no flag — they're treated as ground-state credibility.

    The flag returns with a leading space so it slots into the existing
    template positions: ``**[confidence]**{flag} {claim}``. Empty string
    when no rule fires, so the format collapses cleanly.
    """
    dtype = assertion.get("derivation_type") or ""
    if dtype == "quotation":
        return " [verbatim-quote]"
    uris = assertion.get("evidence_uris") or []
    if isinstance(uris, list) and any(_is_uri_shaped(u) for u in uris):
        return " [primary-source-backed]"
    if dtype == "inference":
        return " [derived]"
    return ""


def build_subgraph_markdown(
    *,
    root_id: str,
    cards: dict[str, dict[str, Any]],
    descriptions: dict[str, str],
    statuses: dict[str, str],
    entity_objs: list[RenderedEntity],
    edges: list[RenderedEdge],
    hops: int,
    top_k_assertions: int,
) -> str:
    """Render the spec markdown template.

    Deterministic: only data-driven values appear; ``generated_at`` is on
    the envelope, not in this text. Two consecutive renders of unchanged
    graph state produce byte-identical output.
    """
    parts: list[str] = []
    parts.extend(_render_root(root_id, cards, descriptions, statuses))
    parts.extend(_render_root_assertions(cards[root_id], top_k_assertions))
    parts.extend(
        _render_related(
            root_id=root_id,
            cards=cards,
            descriptions=descriptions,
            statuses=statuses,
            entity_objs=entity_objs,
            edges=edges,
            hops=hops,
        )
    )
    return "\n\n".join(parts) + "\n"


def _render_root(
    root_id: str,
    cards: dict[str, dict[str, Any]],
    descriptions: dict[str, str],
    statuses: dict[str, str],
) -> list[str]:
    root_card = cards[root_id]
    freshness = root_card.get("freshness") or {}
    updated = freshness.get("updated_at", "") if isinstance(freshness, dict) else ""
    lines: list[str] = [
        f"# {root_card['name']}",
        f"**Type:** {root_card['type']} | "
        f"**Status:** {statuses.get(root_id, '')} | "
        f"**Updated:** {updated}",
    ]
    desc = _escape_md(descriptions.get(root_id, ""))
    if desc:
        lines.append(desc)
    # State signals block (V1.5): surface predicate_summary — the
    # highest-density navigational signal on the card — between the
    # description and Active Assertions. Omitted when empty (e.g.
    # tombstone-only cards have no aggregated forms to render).
    psum = root_card.get("predicate_summary") or ""
    if psum:
        lines.append("## State signals")
        lines.append(_escape_md(psum))
    return lines


def _render_root_assertions(
    root_card: dict[str, Any], top_k_assertions: int
) -> list[str]:
    parts: list[str] = [f"## Active Assertions (top {top_k_assertions})"]
    assertions = root_card.get("top_k_assertions") or []
    if not assertions:
        parts.append("_(no active assertions)_")
        return parts
    assertion_lines: list[str] = []
    for a in assertions:
        claim = _escape_md(str(a.get("claim", "")))
        conf = a.get("confidence", "")
        observed = a.get("observed_at") or ""
        flag = _provenance_flag(a)
        assertion_lines.append(f"- **[{conf}]**{flag} {claim}")
        if observed:
            assertion_lines.append(f"  - *Observed:* {observed}")
    parts.append("\n".join(assertion_lines))
    return parts


def _render_related(
    *,
    root_id: str,
    cards: dict[str, dict[str, Any]],
    descriptions: dict[str, str],
    statuses: dict[str, str],
    entity_objs: list[RenderedEntity],
    edges: list[RenderedEdge],
    hops: int,
) -> list[str]:
    related_count = len(entity_objs) - 1
    parts: list[str] = [f"## Related Entities ({related_count} found, {hops}-hop)"]
    if not edges:
        parts.append("_(no related entities)_")
        return parts
    hop_by_id = {e.entity_id: e.hop_distance for e in entity_objs}
    for type_id, group in groupby(edges, key=lambda e: e.type_id):
        parts.append(f"### {type_id}")
        for edge in group:
            parts.append(
                _render_edge_block(
                    edge=edge,
                    root_id=root_id,
                    cards=cards,
                    descriptions=descriptions,
                    statuses=statuses,
                    hop_by_id=hop_by_id,
                )
            )
    return parts


def _render_edge_block(
    *,
    edge: RenderedEdge,
    root_id: str,
    cards: dict[str, dict[str, Any]],
    descriptions: dict[str, str],
    statuses: dict[str, str],
    hop_by_id: dict[str, int],
) -> str:
    other_id = _pick_other_endpoint(edge, root_id)
    arrow = _DIRECTION_ARROW.get(edge.direction_from_root, "\u2192")
    tcard = cards[other_id]
    t_updated = (tcard.get("freshness") or {}).get("updated_at", "")
    t_hop = hop_by_id.get(other_id, 0)
    block_lines: list[str] = [
        f"#### {arrow} {tcard['name']} (`{other_id}`)",
        f"**Status:** {statuses.get(other_id, '')} | "
        f"**Updated:** {t_updated} | **Hop:** {t_hop}",
    ]
    tdesc = _escape_md(descriptions.get(other_id, ""))
    if tdesc:
        block_lines.append(tdesc)
    t_assertions = tcard.get("top_k_assertions") or []
    if t_assertions:
        top = t_assertions[0]
        flag = _provenance_flag(top)
        block_lines.append(
            f"**Top assertion:** [{top.get('confidence', '')}]{flag} "
            f"{_escape_md(str(top.get('claim', '')))}"
        )
    block_lines.append("---")
    return "\n\n".join(block_lines)


def _pick_other_endpoint(edge: RenderedEdge, root_id: str) -> str:
    """Choose which endpoint to render as the 'target' of an edge.

    For root-incident edges, render the non-root endpoint. For cross /
    sibling edges (both endpoints non-root), render the lexicographically
    larger id — deterministic choice with no semantic weight.
    """
    if edge.source_id == root_id:
        return edge.target_id
    if edge.target_id == root_id:
        return edge.source_id
    return max(edge.source_id, edge.target_id)


def _escape_md(text: str) -> str:
    """Lightweight markdown escape per V1.1 spec.

    Defangs structural heading lines and triple-backtick code fences in
    user content. Not a full sanitizer — just protects the document
    skeleton from user assertions / descriptions that contain ``#`` or
    triple-backticks.
    """
    if not text:
        return ""
    out: list[str] = []
    for line in str(text).split("\n"):
        if line.lstrip().startswith("#"):
            out.append("\\" + line)
        else:
            out.append(line)
    return "\n".join(out).replace("```", "\\`\\`\\`")
