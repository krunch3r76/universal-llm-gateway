"""Archive verdict fallback when skill_receipt_unverified (a:27142)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.cdp_archive_verdict_fallback import (
    apply_archive_verdict_fallback,
    parse_archive_verdict,
)
from claude_bundles.cdp_model_endpoint import CdpGenerateResult

pytestmark = pytest.mark.offline

_BAB9_BODY = """# CDP ask harvest

- execution_id: `bab9e73ad46b4eb69f36ef78c471032f`
- outcome: failed
- error: skill_receipt_unverified: undelivered=['reasoning-posture']

## Body

VERDICT: APPROVE range=aaaa..bbbb head=bbbb

Seat review complete.
"""


def test_parse_archive_verdict_approve() -> None:
    assert parse_archive_verdict(_BAB9_BODY) == "APPROVE"


def test_archive_verdict_promotes_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "cdp-ask-archive-bab9e73a.md"
    archive.write_text(_BAB9_BODY, encoding="utf-8")
    uri = f"file://{archive}"

    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="e" * 32,
        satellite_execution_id="s" * 32,
        prompt_uri="cortex://x",
        picker_model="opus",
        stall_stage="observer_unverified",
        error="skill_receipt_unverified: panel timeout",
        archive_uri=uri,
    )
    out = apply_archive_verdict_fallback(result)
    assert out.ok is True
    assert out.extras.get("verdict_source") == "archive"
    assert out.extras.get("archive_verdict") == "APPROVE"


def test_no_archive_stays_unverified() -> None:
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="e" * 32,
        satellite_execution_id="s" * 32,
        prompt_uri="cortex://x",
        picker_model="opus",
        error="skill_receipt_unverified: empty",
        archive_uri=None,
    )
    out = apply_archive_verdict_fallback(result)
    assert out.ok is False
    assert "verdict_source" not in (out.extras or {})


def test_file_proof() -> None:
    assert Path(__file__).resolve().name == "test_cdp_archive_verdict_fallback.py"
