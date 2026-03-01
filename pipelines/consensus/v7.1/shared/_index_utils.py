"""Index mapping utilities for filter handlers.

format_numbered_facts groups facts by context_prefix before assigning
sequential indices. When the model returns rendered indices, they must be
mapped back to original list positions before use.
"""

from __future__ import annotations

from typing import Any


def build_rendered_order(facts: list[dict[str, Any]]) -> list[int]:
    """Return original-fact indices in the order format_numbered_facts renders them.

    format_numbered_facts groups facts by context_prefix before assigning
    sequential indices [0..N-1]. The model's returned index N therefore
    references the fact at rendered position N, not verified_facts[N].

    Use this to convert model indices to original list positions:
        rendered_order = build_rendered_order(facts)
        original_idx = rendered_order[model_idx]

    ∀ rendered index r: facts[build_rendered_order(facts)[r]] is the fact
    the model referred to when it returned index r.
    """
    has_prefix = any(
        (f.get("context_prefix") or "").strip() for f in facts if isinstance(f, dict)
    )
    if not has_prefix:
        return list(range(len(facts)))

    order: list[str] = []
    groups: dict[str, list[int]] = {}
    ungrouped: list[int] = []
    for orig_idx, fact in enumerate(facts):
        if not isinstance(fact, dict):
            continue
        if not (fact.get("text") or "").strip():
            continue
        raw_topic = (fact.get("context_prefix") or "").strip()
        if raw_topic:
            topic = raw_topic.title()
            if topic not in groups:
                order.append(topic)
                groups[topic] = []
            groups[topic].append(orig_idx)
        else:
            ungrouped.append(orig_idx)

    rendered: list[int] = []
    for topic in order:
        rendered.extend(groups[topic])
    rendered.extend(ungrouped)
    return rendered
