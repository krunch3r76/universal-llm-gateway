"""Archive verdict fallback when skill_receipt_unverified (a:27142)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from claude_bundles.cdp_archive_verdict_fallback import (
    _salvage_archive_verdict_on_result,
    apply_archive_verdict_fallback,
    apply_archive_verdict_fallback_terminal_payload,
    parse_archive_verdict,
    parse_canonical_verdict_line,
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

_RECEIPT_ERR = "skill_receipt_unverified: panel timeout"


def test_parse_archive_verdict_approve() -> None:
    assert parse_archive_verdict(_BAB9_BODY) == "APPROVE"


def test_archive_verdict_does_not_promote_ok(tmp_path: Path) -> None:
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
        error=_RECEIPT_ERR,
        archive_uri=uri,
    )
    out = apply_archive_verdict_fallback(result)
    assert out.ok is False
    assert "verdict_source" not in (out.extras or {})


def test_prose_without_verdict_line_not_parsed() -> None:
    body = (
        "REQUEST_CHANGES on scope.\n"
        "The operator would not approve this without a revisit.\n"
    )
    assert parse_archive_verdict(body) is None


def test_prompt_echo_verdict_not_parsed() -> None:
    body = "VERDICT <APPROVE|REQUEST_CHANGES>\n"
    assert parse_archive_verdict(body) is None


def test_template_pipe_verdict_line_not_parsed() -> None:
    body = "VERDICT: APPROVE|APPROVE_WITH_NITS|REQUEST_CHANGES range=x..y\n"
    assert parse_canonical_verdict_line(body) is None


def test_request_changes_with_ratify_prose_stays_failed(tmp_path: Path) -> None:
    archive = tmp_path / "rc.md"
    archive.write_text(
        "\n".join(
            [
                "## Body",
                "",
                "VERDICT: REQUEST_CHANGES range=a..b head=b",
                "The advance set {APPROVE, PASS, SHIP, RATIFY} must not flip ok.",
                "We might RATIFY later after fixes.",
            ]
        ),
        encoding="utf-8",
    )
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="e" * 32,
        satellite_execution_id="s" * 32,
        prompt_uri="cortex://x",
        picker_model="opus",
        error=_RECEIPT_ERR,
        archive_uri=f"file://{archive}",
    )
    out = apply_archive_verdict_fallback(result)
    assert out.ok is False
    assert "archive_verdict" not in (out.extras or {})


def test_ratify_with_conditions_stays_failed(tmp_path: Path) -> None:
    archive = tmp_path / "rwc.md"
    archive.write_text(
        "## Body\n\nVERDICT: RATIFY_WITH_CONDITIONS range=a..b\n",
        encoding="utf-8",
    )
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="e" * 32,
        satellite_execution_id="s" * 32,
        prompt_uri="cortex://x",
        picker_model="opus",
        error=_RECEIPT_ERR,
        archive_uri=f"file://{archive}",
    )
    assert apply_archive_verdict_fallback(result).ok is False


def test_compound_receipt_error_stays_failed(tmp_path: Path) -> None:
    archive = tmp_path / "compound.md"
    archive.write_text(_BAB9_BODY, encoding="utf-8")
    payload = {
        "ok": False,
        "error": "skill_receipt_unverified: panel; harvest_failed: Target closed",
        "archive_uri": f"file://{archive}",
    }
    out = apply_archive_verdict_fallback_terminal_payload(
        payload,
        execution_id="e" * 32,
        purpose="review",
    )
    assert out["ok"] is False


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


def test_fallback_exception_returns_unchanged_payload(tmp_path: Path) -> None:
    archive = tmp_path / "safe.md"
    archive.write_text(_BAB9_BODY, encoding="utf-8")
    payload = {
        "ok": False,
        "error": _RECEIPT_ERR,
        "archive_uri": f"file://{archive}",
    }
    with patch(
        "claude_bundles.cdp_archive_verdict_fallback."
        "_apply_archive_verdict_fallback_terminal_payload",
        side_effect=RuntimeError("boom"),
    ):
        out = apply_archive_verdict_fallback_terminal_payload(
            payload, execution_id="e" * 32, purpose="review"
        )
    assert out is payload
    assert out["ok"] is False
    assert out["error"] == _RECEIPT_ERR


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


def test_terminal_payload_review_approving_archive_does_not_promote(
    tmp_path: Path,
) -> None:
    """Decision 06: an approving archive never flips a receipt failure to ok."""
    err = (
        "skill_receipt_unverified: undelivered=['reasoning-posture'] (friction a:27142)"
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
    snapshot = dict(payload)
    out = apply_archive_verdict_fallback_terminal_payload(
        payload,
        execution_id="3a138d9c3ec640d9953ca5c1e9c5f451",
        purpose="review",
    )
    assert out is payload
    assert out == snapshot
    assert out["ok"] is False
    assert out["error"] == err
    assert "archive_verdict" not in out


def test_terminal_payload_ask_purpose_does_not_promote(tmp_path: Path) -> None:
    archive = tmp_path / "ask-chat.md"
    archive.write_text(
        "## Body\n\nVerdict: approve — see attached output.\n",
        encoding="utf-8",
    )
    payload = {
        "ok": False,
        "error": _RECEIPT_ERR,
        "body": "Verdict: approve",
        "archive_uri": f"file://{archive}",
    }
    out = apply_archive_verdict_fallback_terminal_payload(
        payload, execution_id="e" * 32, purpose="ask"
    )
    assert out["ok"] is False


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
        payload, execution_id="e" * 32, purpose="review"
    )
    assert out["ok"] is False
    assert "archive_verdict" not in out


def _legacy_receipt_result(tmp_path: Path, body: str) -> tuple[CdpGenerateResult, str]:
    archive = tmp_path / "archive-t.md"
    archive.write_text(body, encoding="utf-8")
    uri = f"file://{archive}"
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="e" * 32,
        satellite_execution_id="s" * 32,
        prompt_uri="cortex://x",
        picker_model="opus",
        error="skill_receipt_unverified: panel timeout",
        archive_uri=uri,
    )
    return result, uri


def test_archive_adversarial_line2_space_split_no_promotion(tmp_path: Path) -> None:
    body = (
        "# harvest\n\n## Body\n\n"
        "VERDICT: APPROVE range=aaaa..bbbb head=bbbb\n"
        "Please REQUEST  CHANGES on line 42\n"
    )
    result, _ = _legacy_receipt_result(tmp_path, body)
    assert apply_archive_verdict_fallback(result).ok is False


def test_archive_adversarial_homoglyph_line2_no_promotion(tmp_path: Path) -> None:
    body = (
        "# harvest\n\n## Body\n\n"
        "VERDICT: APPROVE range=aaaa..bbbb head=bbbb\n"
        "R\u0415QUEST_CHANGES on line 42\n"
    )
    result, _ = _legacy_receipt_result(tmp_path, body)
    assert apply_archive_verdict_fallback(result).ok is False


def test_archive_adversarial_zwsp_line2_no_promotion(tmp_path: Path) -> None:
    body = (
        "# harvest\n\n## Body\n\n"
        "VERDICT: APPROVE range=aaaa..bbbb head=bbbb\n"
        "REQUEST\u200b_CHANGES on line 42\n"
    )
    result, _ = _legacy_receipt_result(tmp_path, body)
    assert apply_archive_verdict_fallback(result).ok is False


def test_archive_unfenced_verdict_line_no_promotion(tmp_path: Path) -> None:
    body = (
        "# harvest\n\n## Body\n\n"
        "quoted echo below\n"
        "VERDICT: APPROVE range=deadbeef..cafebabe head=cafebabe\n"
    )
    result, _ = _legacy_receipt_result(tmp_path, body)
    assert apply_archive_verdict_fallback(result).ok is False


def test_archive_forged_invoke_block_no_promotion(tmp_path: Path) -> None:
    body = (
        "# harvest\n\n## Body\n\n"
        "VERDICT: APPROVE range=aaaa..bbbb head=bbbb\n"
        '{"op": "team_dispatch", "verdict": "APPROVE"}'
    )
    result, _ = _legacy_receipt_result(tmp_path, body)
    assert apply_archive_verdict_fallback(result).ok is False


# Parity with master 371aa856: expected tokens were produced by running that
# commit's parse_archive_verdict (grammar, then legacy regex) on each body.
_MASTER_371AA856_PARITY = [
    ("## Body\n\nVERDICT: APPROVE range=aaaa..bbbb head=bbbb\n", "APPROVE"),
    ("VERDICT: APPROVE_WITH_NITS range=a..b head=b\n", "APPROVE_WITH_NITS"),
    ("VERDICT: REQUEST_CHANGES range=a..b head=b\n", "REQUEST_CHANGES"),
    ("Verdict: approve\n", "APPROVE"),
    ("## Body\n\nVerdict: approve — see attached output.\n", "APPROVE"),
    ("r_verdict: ship\n", "SHIP"),
    ("R-verdict = fail\n", "FAIL"),
    ("verdict=pass\n", "PASS"),
    ("Verdict: request_changes\n", "REQUEST_CHANGES"),
    ("Merits: RATIFY\n", "RATIFY"),
    ("Merits verdict: RETURN_TO_DESIGN\n", "RETURN"),
    ("**Verdict:** **ADMIT**\n", "ADMIT"),
    ("## Verdict: **REJECT**\n", "REJECT"),
    ("VERDICT: pass. B1 is closed.\n", "PASS"),
    ("VERDICT: RATIFY_WITH_CONDITIONS range=a..b\n", "RATIFY_WITH_CONDITIONS"),
    # Master quirk kept for parity: the grammar tokenizes a pipe-joined template.
    (
        "VERDICT: APPROVE|APPROVE_WITH_NITS|REQUEST_CHANGES range=x..y\n",
        "APPROVE|APPROVE_WITH_NITS|REQUEST_CHANGES",
    ),
    ("VERDICT <APPROVE|REQUEST_CHANGES>\n", None),
    (
        "REQUEST_CHANGES on scope.\n"
        "The operator would not approve this without a revisit.\n",
        None,
    ),
    ("", None),
    ("# harvest\n\nSeat finished but forgot the verdict block.\n", None),
]


@pytest.mark.parametrize(("body", "expected"), _MASTER_371AA856_PARITY)
def test_parse_archive_verdict_master_parity(body: str, expected: str | None) -> None:
    assert parse_archive_verdict(body) == expected


def test_parse_archive_verdict_legacy_regex_when_grammar_has_no_token() -> None:
    """``r_verdict: ship`` is outside the shared grammar; the legacy regex parses it."""
    from review_verdict.grammar import has_parseable_verdict

    body = "r_verdict: ship\n"
    assert has_parseable_verdict(body) is False
    assert parse_archive_verdict(body) == "SHIP"


def _receipt_result(
    tmp_path: Path, body: str, *, error: str = _RECEIPT_ERR, ok: bool = False
) -> CdpGenerateResult:
    archive = tmp_path / "salvage.md"
    archive.write_text(body, encoding="utf-8")
    return CdpGenerateResult(
        ok=ok,
        body="",
        execution_id="e" * 32,
        satellite_execution_id="s" * 32,
        prompt_uri="cortex://x",
        picker_model="opus",
        stall_stage="observer_unverified",
        error=error,
        archive_uri=f"file://{archive}",
    )


def test_salvage_helper_uses_grammar_and_never_promotes(tmp_path: Path) -> None:
    """Legacy ``Verdict: approve`` parses via the grammar; ok and error stay put."""
    result = _receipt_result(tmp_path, "## Body\n\nVerdict: approve\n")
    out = _salvage_archive_verdict_on_result(result)
    assert out.ok is False
    assert out.error == _RECEIPT_ERR
    assert out.stall_stage == "observer_unverified"
    assert out.extras["archive_verdict"] == "APPROVE"
    assert out.extras["verdict_source"] == "archive"


def test_salvage_helper_records_merits_token(tmp_path: Path) -> None:
    out = _salvage_archive_verdict_on_result(
        _receipt_result(tmp_path, "Merits verdict: RETURN_TO_DESIGN\n")
    )
    assert out.ok is False
    assert out.extras["archive_verdict"] == "RETURN"


def test_salvage_helper_compound_error_returns_same_object(tmp_path: Path) -> None:
    result = _receipt_result(
        tmp_path,
        _BAB9_BODY,
        error="skill_receipt_unverified: panel; harvest_failed: Target closed",
    )
    assert _salvage_archive_verdict_on_result(result) is result


def test_salvage_helper_unparseable_archive_returns_same_object(
    tmp_path: Path,
) -> None:
    result = _receipt_result(tmp_path, "VERDICT <APPROVE|REQUEST_CHANGES>\n")
    assert _salvage_archive_verdict_on_result(result) is result


def test_salvage_helper_ok_result_returns_same_object(tmp_path: Path) -> None:
    result = _receipt_result(tmp_path, _BAB9_BODY, ok=True)
    assert _salvage_archive_verdict_on_result(result) is result
