"""House carrier fields on conductor context types."""

from __future__ import annotations

from implement_admission.conductor_context import (
    RematerializeContext,
    rematerialize_context_from_dispatch,
)


def test_house_policy_carriers_round_trip() -> None:
    ctx = rematerialize_context_from_dispatch(
        hop_seq=1,
        generation_options={
            "house_induction_binds": ["bind-a"],
            "house_critic_model": "anthropic/claude-sonnet-5-5",
            "house_critic_model_knobs": {"effort": "high"},
        },
    )
    assert ctx is not None
    assert ctx.house_induction_binds == ("bind-a",)
    assert ctx.house_critic_model == "anthropic/claude-sonnet-5-5"
    assert ctx.house_critic_model_knobs == {"effort": "high"}
    bad = rematerialize_context_from_dispatch(
        hop_seq=1,
        generation_options={"house_induction_binds": [1, 2]},
    )
    assert bad is not None
    assert bad.house_induction_binds == ()


def test_carriers_alone_make_rematerialize_context() -> None:
    ctx = rematerialize_context_from_dispatch(
        generation_options={"house_critic_model": "anthropic/claude-sonnet-5-5"},
    )
    assert ctx is not None


def test_effective_summoning_idempotent() -> None:
    rem = RematerializeContext(summoning_thread_id="tid-1")
    first = rem.effective_summoning(top_level_summoning_thread_id="top-9")
    second = rem.effective_summoning(top_level_summoning_thread_id=first[0])
    assert first == second == ("top-9", False)


def test_effective_summoning_unresolved_flag() -> None:
    rem = RematerializeContext(summoning_thread_id_unresolved=True)
    first = rem.effective_summoning(top_level_summoning_thread_id="any")
    second = rem.effective_summoning(top_level_summoning_thread_id=first[0])
    assert first == second == (None, True)
