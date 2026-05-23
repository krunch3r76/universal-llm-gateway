"""gen_rules — neutral-canonical source-to-target projection for agent rules.

See `agent-surface/README.md` and `tmp/prompts/phase3-neutral-canonical-design.md`
(historical design v3) for the spec.
"""

from .check import diff_against, extract_core_subagent_table
from .parser import Block, ParsedSource, parse_source
from .renderer import render_agents_md_section, render_cursor_mdc, splice_agents_md

__all__ = [
    "Block",
    "ParsedSource",
    "diff_against",
    "extract_core_subagent_table",
    "parse_source",
    "render_agents_md_section",
    "render_cursor_mdc",
    "splice_agents_md",
]
