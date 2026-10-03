"""Guard: team_dispatch( examples in named loci match the MCP signature."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from claude_bundles.team_dispatch_call_guard import (
    call_bodies,
    call_example_keywords,
    frontier_descriptor_bad_mcp_call_syntax,
    mcp_tool_param_names,
)

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_FRONTIER = _REPO / "services/mcp-server/tools/frontier.py"
_CSE_SESSION = _REPO / "services/mcp-server/tools/cse_session.py"

# Friction a:37288 handoff §3 — only add paths with an exemption note in closeout.
_LOCI_REL = (
    "services/mcp-server/tools/frontier.py",
    "cursor-plugins/ulg-ecosystem/skills/liaison/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/liaison-cursor/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/consult-routing/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/consult-routing/routing-detail-annex.md",
    "cursor-plugins/ulg-ecosystem/skills/claude-ai-cdp-navigation/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/claude-ai-cdp-navigation/reference-annex.md",
    "cursor-plugins/ulg-ecosystem/skills/checkpoint-discipline/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/conductor/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/conductor/reference-admit.md",
    "cursor-plugins/ulg-ecosystem/skills/conductor/reference-packet.md",
    "cursor-plugins/ulg-ecosystem/skills/conductor/reference-run-to-completion.md",
    "cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/SKILL.md",
    "cursor-plugins/ulg-ecosystem/commands/conductor.md",
    "scripts/model_manager/ui/controller/charter_runner/tick_sos.py",
    "pipelines/operator_hop_harvest/v1/handlers/assemble.py",
)


def test_loci_team_dispatch_keywords_match_mcp_signature() -> None:
    allowed = mcp_tool_param_names(_FRONTIER, "team_dispatch")
    assert allowed, "frontier.py team_dispatch signature was empty"
    unknown: list[str] = []
    for rel in _LOCI_REL:
        path = _REPO / rel
        assert path.is_file(), f"missing locus file {rel}"
        for body in call_bodies(path.read_text(encoding="utf-8")):
            for name in call_example_keywords(body):
                if name not in allowed:
                    unknown.append(f"{rel}: {name}")
    assert not unknown, (
        "team_dispatch example keywords absent from the MCP signature: "
        + ", ".join(sorted(set(unknown)))
    )


def test_frontier_team_dispatch_descriptor_uses_contract_and_purpose() -> None:
    text = _FRONTIER.read_text(encoding="utf-8")
    hits = frontier_descriptor_bad_mcp_call_syntax(text)
    assert not hits, (
        "frontier.py team_dispatch descriptor still uses backticked job=/session= "
        "as MCP call syntax: " + "; ".join(hits)
    )
    # a:37288 — say once how MCP names map onto the Stargate body.
    assert "MCP `contract` forwards as Stargate body `job`" in text, (
        "team_dispatch docstring missing contract→job wire note"
    )
    assert "`purpose` forwards as body `purpose` on generate only." in text, (
        "team_dispatch docstring missing generate-only purpose wire note"
    )


# a:37328 — canonical fol + orientation tips. Call syntax is the substring
# ``job=`` (bare or backticked). The cited pre-fix lines were bare
# ``job=freeform`` inside team_dispatch examples; a backtick-only scan stays
# green on that specimen. Stargate body ``job`` and ``job_vocab`` have no
# ``job=`` and stay.
_TEACHING_SURFACES_REL = (
    "config/mcp/canonical.yaml",
    "services/mcp-server/tools/_oc_surface_templates.py",
    "services/mcp-server/tools/continuity.py",
)
_JOB_CALL_SYNTAX = re.compile(r"job=")


def _job_call_syntax_hits(label: str, text: str) -> list[str]:
    return [
        f"{label}:{lineno}"
        for lineno, line in enumerate(text.splitlines(), 1)
        if _JOB_CALL_SYNTAX.search(line)
    ]


def test_mcp_teaching_surfaces_omit_job_call_syntax() -> None:
    bare = (
        "team_dispatch(op=generate, seat=cursor-sdk, model=cursor/grok-4.7,\n"
        "job=freeform, dispatch_thread_id=…, lane=B)\n"
    )
    assert _job_call_syntax_hits("specimen", bare)
    assert _job_call_syntax_hits("specimen", "still shows `job=freeform`")
    assert not _job_call_syntax_hits(
        "specimen",
        "explicit job → source_ref; (job_vocab); body `job`; when job omitted",
    )
    hits: list[str] = []
    for rel in _TEACHING_SURFACES_REL:
        path = _REPO / rel
        assert path.is_file(), f"missing teaching surface {rel}"
        hits.extend(_job_call_syntax_hits(rel, path.read_text(encoding="utf-8")))
    assert not hits, "MCP teaching surfaces still use job= call syntax: " + ", ".join(
        hits
    )


def test_claude_ai_cdp_navigation_cse_session_example_matches_schema() -> None:
    path = _REPO / "cursor-plugins/ulg-ecosystem/skills/claude-ai-cdp-navigation/SKILL.md"
    allowed = mcp_tool_param_names(_CSE_SESSION, "cse_session")
    unknown: list[str] = []
    for body in call_bodies(path.read_text(encoding="utf-8"), needle="cse_session("):
        for name in call_example_keywords(body):
            if name not in allowed:
                unknown.append(name)
    assert not unknown, (
        "cse_session example keywords absent from the MCP signature: "
        + ", ".join(sorted(set(unknown)))
    )
