"""Recipient vs unstamped labeling in boot unread extraction."""

from __future__ import annotations

from tools.cortex_named_tools._boot_summarize import build_unread_threads


def test_toc_rows_labeled_unread() -> None:
    rows = build_unread_threads(
        [{"thread": "1", "slug": "toc", "unread_count": 3}]
    )
    assert rows == [{"id": "1", "slug": "toc", "unread": 3}]


def test_thread_wide_detail_labeled_unstamped() -> None:
    rows = build_unread_threads(
        [
            {
                "id": "2",
                "slug": "wide",
                "unread_count": 9,
                "unread_basis": {
                    "basis": "read_at_null",
                    "recipient": None,
                    "includes_superseded": False,
                    "as_of": "2026-10-06T00:00:00Z",
                    "source": "agent_bus_store.threads",
                },
            }
        ]
    )
    assert rows == [{"id": "2", "slug": "wide", "unstamped": 9}]


def test_scoped_detail_labeled_unread() -> None:
    rows = build_unread_threads(
        [
            {
                "id": "3",
                "slug": "scoped",
                "unread_count": 1,
                "unread_basis": {
                    "basis": "read_at_null",
                    "recipient": "cursor",
                    "includes_superseded": False,
                    "as_of": "2026-10-06T00:00:00Z",
                    "source": "agent_bus_store.threads",
                },
            }
        ]
    )
    assert rows == [{"id": "3", "slug": "scoped", "unread": 1}]
