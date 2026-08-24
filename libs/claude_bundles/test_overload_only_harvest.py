"""Error-banner-only harvest bodies are not empty-CSE proof (a:30408 / a:30411)."""

from claude_bundles.overload_only_harvest import is_error_banner_only_harvest

_BANNER_529 = (
    "Claude responded: API Error: 529 Overloaded.\n\n"
    "API Error: 529 Overloaded. This is a server-side issue, usually temporary "
    "— try again in a moment. If it persists, check https://status.claude.com."
)


def test_529_archive_fixture_is_banner_only() -> None:
    assert is_error_banner_only_harvest(_BANNER_529) is True


def test_500_banner_is_banner_only() -> None:
    assert (
        is_error_banner_only_harvest(
            "Claude responded: API Error: 500 Internal Server Error."
        )
        is True
    )


def test_quoted_529_in_prose_is_not_banner_only() -> None:
    assert (
        is_error_banner_only_harvest(
            "Done. API Error: 529 was transient during the run."
        )
        is False
    )
