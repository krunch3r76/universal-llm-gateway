"""Grant gate parity with paste_as_operator."""

from web_tab.grants import require_write_authorization


def test_explicit_grant_requires_authorization_source() -> None:
    refused = require_write_authorization("explicit", None)
    assert refused is not None
    assert refused["code"] == "grant_or_source_required"


def test_operator_grant_requires_authorization_source() -> None:
    refused = require_write_authorization("operator", "")
    assert refused is not None
    assert refused["code"] == "grant_or_source_required"


def test_grant_with_source_accepted() -> None:
    assert require_write_authorization("explicit", "agent-bus:21642#13") is None
