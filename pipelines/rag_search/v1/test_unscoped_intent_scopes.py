"""Unscoped writing-intent scope expansion (assertion:38634)."""

from __future__ import annotations

from pipelines.rag_search.v1.unscoped_intent_scopes import (
    classify_unscoped_writing_intent,
    expand_default_scopes_for_query,
)


def test_ceremonial_gratitude_query_expands_from_all() -> None:
    query = (
        "ceremonial LLM closes and gratitude endings for a formal talk — "
        "thank you for your attention style"
    )
    scopes, expanded = expand_default_scopes_for_query(query, ["all"])
    assert expanded is True
    assert scopes == [
        "writing",
        "writing_exemplars",
        "llm_writing_anti",
        "research",
    ]
    assert classify_unscoped_writing_intent(query) is True


def test_agent_session_close_query_stays_on_all() -> None:
    query = "session-close-kernel protocol for cortex session_close preflight"
    scopes, expanded = expand_default_scopes_for_query(query, ["all"])
    assert expanded is False
    assert scopes == ["all"]
    assert classify_unscoped_writing_intent(query) is False


def test_explicit_scope_override_list_not_expanded() -> None:
    query = "ceremonial close with gratitude"
    scopes, expanded = expand_default_scopes_for_query(query, ["llm_prompting"])
    assert expanded is False
    assert scopes == ["llm_prompting"]
