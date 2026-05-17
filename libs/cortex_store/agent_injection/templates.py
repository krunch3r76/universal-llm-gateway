"""D.1/D.2/D.3/D.4 pure renderers per Appendix D (v1.3.2-amended).

All templates are structural-grade or belief-grade syntax; no prose laundering.
"""

from __future__ import annotations

from .errors import TemplateRenderError

# Required exact substrings — quality gate greps for these inside this file
D1_TEMPLATE = """[STRUCTURED_LOOKUP | source: assertion {assertion_id} | confidence: {confidence_score} | valid_from: {valid_from} | checked: {utc_now}]
Field: {field_name}
Value: {claim_value}
[/STRUCTURED_LOOKUP]
"""

D2_TEMPLATE = """[CONTEXT_PROVISION
  | entity: {entity_id}
  | included_count: {included_count}
  | total_active_count: {total_active_count}
  | truncated: {truncated}
  | selection_strategy: {selection_strategy}
  | selection_params: {selection_params}
  | pulled_at: {pulled_at}
  | cursor: {cursor}
  | content_hash: {content_hash}
]
{rows_block}
[/CONTEXT_PROVISION]
"""

D3_TEMPLATE = """[TEMPORAL_QUALIFIED | assertion_id: {assertion_id} | valid_from: {valid_from} | valid_until: {valid_until} | now: {utc_now} | freshness: {freshness}]
Claim: {claim}
[/TEMPORAL_QUALIFIED]
"""

D4_TEMPLATE = """[BELIEF_INJECTION | assertion_id: {assertion_id} | confidence: {confidence_score} | derivation: {derivation_type} | seeded_by: {seeded_by} | seeded_at: {seeded_at}]
Claim: {claim}
Reasoning: {reasoning_summary}
[/BELIEF_INJECTION]
"""


def render_d1(ctx: dict) -> str:
    """Render D.1 STRUCTURED_LOOKUP block. Raises TemplateRenderError on missing key."""
    try:
        return D1_TEMPLATE.format(**ctx)
    except KeyError as exc:
        raise TemplateRenderError(f"D.1 missing key: {exc}") from exc


def render_d2(ctx: dict) -> str:
    """Render D.2 CONTEXT_PROVISION block. rows_block supplied by materializer."""
    try:
        return D2_TEMPLATE.format(**ctx)
    except KeyError as exc:
        raise TemplateRenderError(f"D.2 missing key: {exc}") from exc


def render_d3(ctx: dict) -> str:
    """Render D.3 TEMPORAL_QUALIFIED block."""
    try:
        return D3_TEMPLATE.format(**ctx)
    except KeyError as exc:
        raise TemplateRenderError(f"D.3 missing key: {exc}") from exc


def render_d4(ctx: dict) -> str:
    """Render D.4 BELIEF_INJECTION block."""
    try:
        return D4_TEMPLATE.format(**ctx)
    except KeyError as exc:
        raise TemplateRenderError(f"D.4 missing key: {exc}") from exc
