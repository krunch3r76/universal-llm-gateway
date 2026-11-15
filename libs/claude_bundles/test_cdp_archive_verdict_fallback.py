"""Archive verdict fallback when skill_receipt_unverified (a:27142)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.cdp_archive_verdict_fallback import (
    apply_archive_verdict_fallback,
    apply_archive_verdict_fallback_terminal_payload,
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


def test_archive_verdict_promotes_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert out.extras.get("skill_receipt_warning") == result.error


def test_prose_approve_after_request_changes_not_approve() -> None:
    body = (
        "REQUEST_CHANGES on scope.\n"
        "The operator would not approve this without a revisit.\n"
    )
    assert parse_archive_verdict(body) is None


def test_prompt_echo_verdict_not_parsed() -> None:
    body = "VERDICT <APPROVE|REQUEST_CHANGES>\n"
    assert parse_archive_verdict(body) is None


def test_stays_unverified_without_parseable_archive(tmp_path: Path) -> None:
    archive = tmp_path / "archive-no-verdict.md"
    archive.write_text(
        "# harvest\n\nSeat finished but forgot the verdict block.\n",
        encoding="utf-8",
    )
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="e" * 32,
        satellite_execution_id="s" * 32,
        prompt_uri="cortex://x",
        picker_model="opus",
        error="skill_receipt_unverified: missing",
        archive_uri=f"file://{archive}",
    )
    out = apply_archive_verdict_fallback(result)
    assert out.ok is False
    assert "verdict_source" not in (out.extras or {})


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


_882FEF0D_ARCHIVE = """# CDP ask harvest

- execution_id: `3a138d9c3ec640d9953ca5c1e9c5f451`
- stargate_execution_id: `882fef0d-ebd1-4cce-9605-be2243f114a2`
- outcome: failed
- error: skill_receipt_unverified: required skills fail channel attest after attach + inline + induction seal: undelivered=['reasoning-posture', 'consult-posture', 'hypothesize-simulate'] (attached=[], inlined=[], induction=[]) — fail closed (friction a:27142)

## Body

VERDICT: APPROVE_WITH_NITS range=706f4b8dbbb376bdbf3ee62ec3b77c8f62c303db..d2bf6021088fb85eac2d30c5412bc5811ba2fba9 head=d2bf6021088fb85eac2d30c5412bc5811ba2fba9

Seat review complete.
"""


def test_terminal_payload_skill_receipt_promotes_ok(
    tmp_path: Path,
) -> None:
  err = (
      "skill_receipt_unverified: undelivered=['reasoning-posture'] "
      "(friction a:27142)"
  )
  archive = tmp_path / "882fef0d-archive.md"
  archive.write_text(_882FEF0D_ARCHIVE, encoding="utf-8")
  payload = {
      "ok": False,
      "error": err,
      "body": "seat prose",
      "archive_uri": f"file://{archive}",
      "stall_stage": "observer_unverified",
  }
  out = apply_archive_verdict_fallback_terminal_payload(
      payload, execution_id="3a138d9c3ec640d9953ca5c1e9c5f451"
  )
  assert out["ok"] is True
  assert out["archive_verdict"] == "APPROVE_WITH_NITS"
  assert out["skill_receipt_warning"] == err
  assert out.get("error") is None


def test_terminal_payload_without_parseable_verdict_stays_failed(
    tmp_path: Path,
) -> None:
  archive = tmp_path / "no-verdict.md"
  archive.write_text(
      "# CDP ask harvest\n\n- outcome: failed\n\n## Body\n\nno verdict here\n",
      encoding="utf-8",
  )
  payload = {
      "ok": False,
      "error": "skill_receipt_unverified: empty",
      "archive_uri": f"file://{archive}",
  }
  out = apply_archive_verdict_fallback_terminal_payload(
      payload, execution_id="e" * 32
  )
  assert out["ok"] is False
  assert "archive_verdict" not in out
