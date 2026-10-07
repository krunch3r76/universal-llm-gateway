"""Unscoped writing-intent scope expansion (assertion:38634)."""

from __future__ import annotations

import pytest

from pipelines.rag_search.v1.unscoped_intent_scopes import (
    classify_expansion_mode,
    classify_unscoped_writing_intent,
    expand_default_scopes_for_query,
)


_WRITING_ONLY = [
    "writing",
    "writing_exemplars",
    "llm_writing_anti",
    "research",
]


def test_ceremonial_gratitude_query_replaces_all() -> None:
    query = (
        "ceremonial LLM closes and gratitude endings for a formal talk — "
        "thank you for your attention style"
    )
    scopes, expanded = expand_default_scopes_for_query(query, ["all"])
    assert expanded is True
    assert scopes == _WRITING_ONLY
    assert classify_expansion_mode(query) == "replace"


def test_agent_session_close_query_stays_on_all() -> None:
    query = "session-close-kernel protocol for cortex session_close preflight"
    scopes, expanded = expand_default_scopes_for_query(query, ["all"])
    assert expanded is False
    assert scopes == ["all"]


@pytest.mark.parametrize(
    "query",
    [
        "agent bus session closing ritual gratitude",
        "ceremonial close of the orchestrator lane at end of session",
        "review sign-off gate for cursor-sdk lane closeout",
    ],
)
def test_agent_natural_language_probes_do_not_expand(query: str) -> None:
    scopes, expanded = expand_default_scopes_for_query(query, ["all"])
    assert expanded is False
    assert scopes == ["all"]
    assert classify_unscoped_writing_intent(query) is False


def test_finra_closing_statement_stays_on_all() -> None:
    query = "closing statement for FINRA arbitration hearing"
    scopes, expanded = expand_default_scopes_for_query(query, ["all"])
    assert expanded is False
    assert scopes == ["all"]


def test_ghostwriting_replaces_all() -> None:
    query = "ghostwriting a demand letter closing paragraph"
    scopes, expanded = expand_default_scopes_for_query(query, ["all"])
    assert expanded is True
    assert scopes == _WRITING_ONLY


def test_weak_prose_appends_writing_scopes() -> None:
    query = "outbound prose register for a warm closing remark"
    scopes, expanded = expand_default_scopes_for_query(query, ["all"])
    assert expanded is True
    assert scopes[0] == "all"
    assert set(scopes) == {"all", *_WRITING_ONLY}


def test_explicit_scope_override_list_not_expanded() -> None:
    query = "ceremonial close with gratitude"
    scopes, expanded = expand_default_scopes_for_query(query, ["llm_prompting"])
    assert expanded is False
    assert scopes == ["llm_prompting"]
