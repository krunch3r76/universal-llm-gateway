"""Consult substrate notice: who receives it, and that the stamp is once."""

from consult_substrate_notice import (
    SUBSTRATE_NOTICE,
    apply_substrate_notice_if_warranted,
    ensure_substrate_notice,
)


def test_consult_and_sketch_warrant_conductor_does_not() -> None:
    assert apply_substrate_notice_if_warranted("task", "consult").endswith(
        SUBSTRATE_NOTICE + "\n"
    )
    assert SUBSTRATE_NOTICE in apply_substrate_notice_if_warranted("task", "sketch")
    assert apply_substrate_notice_if_warranted("task", "conductor") == "task"
    assert apply_substrate_notice_if_warranted("task", "implement") == "task"
    assert apply_substrate_notice_if_warranted("task", None) == "task"


def test_cdp_ask_and_review_warrant_other_purposes_do_not() -> None:
    assert SUBSTRATE_NOTICE in apply_substrate_notice_if_warranted(
        "task", purpose="ask"
    )
    assert SUBSTRATE_NOTICE in apply_substrate_notice_if_warranted(
        "task", purpose="review"
    )
    assert apply_substrate_notice_if_warranted("task", purpose="produce") == "task"
    assert (
        apply_substrate_notice_if_warranted("task", purpose="operator-proxy") == "task"
    )


def test_ensure_is_idempotent() -> None:
    once = ensure_substrate_notice("body")
    assert ensure_substrate_notice(once) == once
    assert once.count("Primitives in libs/") == 1
