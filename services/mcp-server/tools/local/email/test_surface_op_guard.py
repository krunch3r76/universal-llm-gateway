"""Life-surface read-only gating for email dispatch ops."""

from __future__ import annotations

from request_profile import bind_request

from tools.local.email.surface_op_guard import (
    email_op_allowed_on_surface,
    life_surface_op_denial,
)


def test_life_allows_tier_r_ops() -> None:
    with bind_request("default", surface="life"):
        assert email_op_allowed_on_surface("list")
        assert email_op_allowed_on_surface("recent")
        assert email_op_allowed_on_surface("get")


def test_life_blocks_mutating_ops() -> None:
    with bind_request("default", surface="life"):
        assert not email_op_allowed_on_surface("pull")
        assert not email_op_allowed_on_surface("ingest_one")
        assert not email_op_allowed_on_surface("move")
        assert not email_op_allowed_on_surface("send")


def test_code_allows_all_tiers() -> None:
    with bind_request("default", surface="code"):
        assert email_op_allowed_on_surface("pull")
        assert email_op_allowed_on_surface("review_extract")


def test_life_denial_payload_shape() -> None:
    denial = life_surface_op_denial("move")
    assert denial["error"] == "life_surface_read_only"
    assert denial["surface"] == "life"
    assert denial["op"] == "move"
