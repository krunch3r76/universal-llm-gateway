"""Intent check matrix — refuse-list, hard-outs, refs, questions ceiling."""

from __future__ import annotations

from life_intent.intent_check import check_intent
from life_intent.registry import load_registry


def _resolver(ref: str) -> bool:
    return ref.startswith(("todo:", "cortex://", "agent-bus:"))


def test_valid_investigate_intent() -> None:
    result = check_intent(
        {
            "verb": "investigate",
            "subject": "reminder double-fire",
            "detail": "The reminder notification fires twice every morning around 8am.",
            "refs": ["todo:reminder-fix"],
        },
        ref_resolver=_resolver,
    )
    assert not result.rejects
    assert not result.questions
    assert result.normalized_intent is not None
    assert result.normalized_intent["verb"] == "investigate"


def test_unknown_verb_reject() -> None:
    result = check_intent(
        {
            "verb": "deploy",
            "subject": "something",
            "detail": "This should never route through life delegate.",
        }
    )
    assert result.normalized_intent is None
    assert any(r.code == "unknown_verb" for r in result.rejects)


def test_refused_vocabulary_dispatch() -> None:
    result = check_intent(
        {
            "verb": "fix",
            "subject": "broken thing",
            "detail": "Please team_dispatch op=generate role=cursor-sdk to fix this.",
        }
    )
    assert any(r.code == "refused_vocabulary" for r in result.rejects)


def test_hard_out_implement() -> None:
    result = check_intent(
        {
            "verb": "build",
            "subject": "new feature",
            "detail": "Skip recon and go straight to implement with a dense spec.",
        }
    )
    assert any(r.code == "hard_out" for r in result.rejects)


def test_bad_ref_reject() -> None:
    result = check_intent(
        {
            "verb": "fix",
            "subject": "broken widget",
            "detail": "The widget fails on startup with a clear stack trace in logs.",
            "refs": ["missing:ref"],
        },
        ref_resolver=_resolver,
    )
    assert any(r.code == "bad_ref" for r in result.rejects)


def test_questions_at_most_one_round() -> None:
    result = check_intent(
        {
            "verb": "investigate",
            "subject": "the thing",
            "detail": "It keeps breaking sometimes when I use it.",
        }
    )
    assert not result.rejects
    assert len(result.questions) <= 1


def test_refuse_list_covers_dispatch_tokens() -> None:
    reg = load_registry()
    for token in ("team_dispatch", "contract:", "cursor-sdk"):
        assert token.lower() in reg.refuse_list
