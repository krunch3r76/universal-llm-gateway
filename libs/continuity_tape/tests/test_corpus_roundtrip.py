"""R2 corpus round-trip — offline holds logic + optional live integration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from continuity_tape.corpus_roundtrip import (
    check_corpus_roundtrip,
    sealed_roundtrip_holds,
)

pytestmark = pytest.mark.offline


def test_sealed_roundtrip_exact_match() -> None:
    text = "# Transcript: sid\n\n## Turn 1 — hi\n\n### User\n\nhi\n\n### Assistant\n\nok\n"
    assert sealed_roundtrip_holds(sealed=text, rendered=text, legacy=text) is None


def test_sealed_roundtrip_prefix_extend() -> None:
    sealed = "# Transcript: sid\n\n## Turn 1 — hi\n\n### User\n\nhi\n\n### Assistant\n\nok\n"
    rendered = sealed + "\n## Turn 2 — more\n\n### User\n\nmore\n\n### Assistant\n\n(no assistant output)\n"
    assert sealed_roundtrip_holds(sealed=sealed, rendered=rendered, legacy=rendered) == "prefix_extend"


def test_sealed_roundtrip_assistant_fill() -> None:
    sealed = (
        "# Transcript: sid\n\n## Turn 1 — hi\n\n### User\n\nhi\n\n"
        "### Assistant\n\n(no assistant output)\n"
    )
    rendered = (
        "# Transcript: sid\n\n## Turn 1 — hi\n\n### User\n\nhi\n\n"
        "### Assistant\n\nFilled later.\n"
    )
    assert sealed_roundtrip_holds(sealed=sealed, rendered=rendered, legacy=rendered) == "assistant_fill"


@pytest.mark.integration
def test_live_corpus_roundtrip() -> None:
    db = Path(os.environ.get("CORTEX_DB_PATH", Path.home() / ".cortex/cortex.db"))
    if not db.is_file():
        pytest.skip("live cortex.db unavailable")
    report = check_corpus_roundtrip(cortex_db=db)
    assert report.legacy_parity_diff == 0, report.summary_line()
    assert report.diff_rows == 0, report.summary_line()
