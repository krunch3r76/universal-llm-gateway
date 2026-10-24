"""Tests for inbound source_message premise checks."""

from __future__ import annotations

import pytest

from writing_intake.source_premise import (
    source_message_has_url,
    unsupported_source_claims,
)

pytestmark = pytest.mark.offline


def test_source_message_has_url_detects_http() -> None:
    assert source_message_has_url("See https://example.com/apply")
    assert not source_message_has_url("Hi Kaywan, no link in this note.")


def test_unsupported_link_claim_when_source_has_no_url() -> None:
    source = "Hi Kaywan, we have a role in San Jose that fits your location."
    draft = 'The "apply here" link did not come through on my end.'
    violations = unsupported_source_claims(source_message=source, draft_text=draft)
    assert violations == ["draft:link_missing_claim_without_source_url"]


def test_link_claim_allowed_when_source_contains_url() -> None:
    source = "Apply at https://jobs.example.com/role"
    draft = "The apply link did not come through on my end."
    assert unsupported_source_claims(source_message=source, draft_text=draft) == []


def test_clean_reply_no_violation_without_url_in_source() -> None:
    source = (
        "Hi Kaywan,\n\nWe thought you might be interested in our San Jose role; "
        "your location is a good fit."
    )
    draft = (
        "Thank you for reaching out. I am interested and glad San Jose works for me. "
        "What are the next steps?"
    )
    assert unsupported_source_claims(source_message=source, draft_text=draft) == []
