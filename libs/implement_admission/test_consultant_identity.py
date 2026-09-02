"""Offline tests — (model_identity, rung) independence predicate (P0)."""

from __future__ import annotations

import pytest

from implement_admission.check_review_substrate import (
    UNKNOWN_MODEL_IDENTITY,
    consultant_identity,
    consultant_rung,
    independently_measured,
    model_identity,
)

pytestmark = pytest.mark.offline


def test_alias_fold_cdp_fable_vs_cursor_fable_same_effort_not_independent() -> None:
    cdp_id = consultant_identity("cdp/fable")
    cursor_id = consultant_identity("cursor/claude-fable-5-1")
    cursor_knob = consultant_identity(
        "cursor/claude-fable-5-1", {"effort": "high"}
    )
    assert cdp_id.model_identity == "claude-fable-5-1"
    assert cursor_id.model_identity == "claude-fable-5-1"
    assert cursor_knob.model_identity == "claude-fable-5-1"
    assert cdp_id.rung == "high"
    assert cursor_id.rung == "high"
    assert cursor_knob.rung == "high"
    assert independently_measured(cdp_id, cursor_id) is False
    assert independently_measured(cdp_id, cursor_knob) is False


def test_same_model_different_rung_independent() -> None:
    explicit = consultant_identity("cursor/grok-4.6", {"effort": "xhigh"})
    suffix = consultant_identity("cursor/grok-4.6-high")
    assert explicit.model_identity == "grok-4.6"
    assert suffix.model_identity == "grok-4.6"
    assert explicit.rung == "xhigh"
    assert suffix.rung == "high"
    assert independently_measured(explicit, suffix) is True


def test_cdp_suffix_rungs() -> None:
    opus_extra = consultant_identity("cdp/opus-5-extra")
    opus_base = consultant_identity("cdp/opus-5")
    assert independently_measured(opus_extra, opus_base) is True
    assert opus_base.rung == "high"
    assert opus_extra.rung == "xhigh"

    cdp_max = consultant_identity("cdp/opus-5-max")
    cursor_max = consultant_identity("cursor/claude-opus-5", {"effort": "max"})
    assert independently_measured(cdp_max, cursor_max) is False
    assert cdp_max.rung == "max"
    assert cursor_max.rung == "max"


def test_different_model_same_vendor_independent() -> None:
    terra = consultant_identity("cursor/gpt-5.6-terra")
    sol = consultant_identity("cursor/gpt-5.6-sol")
    assert terra.model_identity != sol.model_identity
    assert independently_measured(terra, sol) is True


def test_unknown_identity_fail_closed() -> None:
    cases = ["cursor/mystery-9", "cdp/not-a-real-model", ""]
    identities = [consultant_identity(m) for m in cases]
    for ident in identities:
        assert ident.model_identity == UNKNOWN_MODEL_IDENTITY
        assert ident.rung is None
    for left in identities:
        for right in identities:
            assert independently_measured(left, right) is False
    assert model_identity("cursor/mystery-9") == UNKNOWN_MODEL_IDENTITY
    assert consultant_rung("cdp/not-a-real-model") is None


def test_no_effort_knob_same_model_fail_closed() -> None:
    composer_a = consultant_identity("cursor/composer-2.5")
    composer_b = consultant_identity("cursor/composer-2.5")
    haiku = consultant_identity("cursor/claude-haiku-4-5")
    assert composer_a.rung is None
    assert composer_b.rung is None
    assert haiku.rung is None
    assert independently_measured(composer_a, composer_b) is False
    assert independently_measured(haiku, haiku) is False


def test_cloud_api_rung_is_none_until_recorded() -> None:
    api = consultant_identity("openai/gpt-5.6-terra")
    cursor = consultant_identity("cursor/gpt-5.6-terra")
    assert api.model_identity == cursor.model_identity == "gpt-5.6-terra"
    assert api.rung is None
    assert cursor.rung == "medium"
    assert independently_measured(api, cursor) is False


def test_reasoning_knob_normalizes_to_wire() -> None:
    assert (
        consultant_rung("cursor/gpt-5.5", {"reasoning": "extra-high"}) == "xhigh"
    )
    assert consultant_rung("cursor/gpt-5.6-sol") == "medium"


def test_non_rung_knobs_ignored() -> None:
    fast_on = consultant_identity("cursor/grok-4.6", {"fast": "true"})
    fast_off = consultant_identity("cursor/grok-4.6", {"fast": "false"})
    assert fast_on.rung == "high"
    assert fast_off.rung == "high"
    assert independently_measured(fast_on, fast_off) is False


def test_cdp_sonnet_default_rung_is_xhigh() -> None:
    assert consultant_rung("cdp/sonnet-5") == "xhigh"
