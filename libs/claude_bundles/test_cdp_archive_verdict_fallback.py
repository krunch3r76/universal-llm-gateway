"""Archive verdict fallback when skill_receipt_unverified (a:27142)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from claude_bundles.cdp_archive_verdict_fallback import (
    apply_archive_verdict_fallback,
    apply_archive_verdict_fallback_gated,
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
        error=_RECEIPT_ERR,
        archive_uri=uri,
    )
    out = apply_archive_verdict_fallback(result)
    assert out.ok is True
    assert out.extras.get("verdict_source") == "archive"
    assert out.extras.get("archive_verdict") == "APPROVE"
    assert out.extras.get("skill_receipt_warning") == result.error


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
        "claude_bundles.cdp_archive_verdict_fallback._merge_promoted_payload",
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


def test_terminal_payload_skill_receipt_promotes_ok(
    tmp_path: Path,
) -> None:
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
    out = apply_archive_verdict_fallback_terminal_payload(
        payload,
        execution_id="3a138d9c3ec640d9953ca5c1e9c5f451",
        purpose="review",
    )
    assert out["ok"] is True
    assert out["archive_verdict"] == "APPROVE_WITH_NITS"
    assert out["skill_receipt_warning"] == err
    assert out.get("error") is None
    assert out.get("harvest_provenance") == "chat"


def test_gated_review_approve_promotes_with_harvest_provenance(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "stargate-review.md"
    archive.write_text(_BAB9_BODY, encoding="utf-8")
    result = CdpGenerateResult(
        ok=False,
        body="",
        execution_id="e" * 32,
        satellite_execution_id="s" * 32,
        prompt_uri="cortex://x",
        picker_model="opus",
        stall_stage="observer_unverified",
        error=_RECEIPT_ERR,
        archive_uri=f"file://{archive}",
    )
    out = apply_archive_verdict_fallback_gated(result, purpose="review")
    assert out.ok is True
    assert out.extras.get("harvest_provenance") == "chat"


def test_gated_ask_purpose_does_not_promote(tmp_path: Path) -> None:
    archive = tmp_path / "stargate-ask.md"
    archive.write_text(_BAB9_BODY, encoding="utf-8")
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
    out = apply_archive_verdict_fallback_gated(result, purpose="ask")
    assert out.ok is False
    assert "archive_verdict" not in (out.extras or {})


def test_gated_unknown_purpose_fail_closed(tmp_path: Path) -> None:
    archive = tmp_path / "stargate-unknown.md"
    archive.write_text(_BAB9_BODY, encoding="utf-8")
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
    out = apply_archive_verdict_fallback_gated(result, purpose=None)
    assert out.ok is False


def test_disagreeing_verdict_lines_stay_failed(tmp_path: Path) -> None:
    archive = tmp_path / "split-verdict.md"
    archive.write_text(
        "\n".join(
            [
                "## Body",
                "",
                "Earlier draft quoted:",
                "VERDICT: APPROVE range=aaa..bbb head=bbb",
                "",
                "Final:",
                "VERDICT: REQUEST_CHANGES range=ccc..ddd head=ddd",
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
    out = apply_archive_verdict_fallback_gated(result, purpose="review")
    assert out.ok is False
    assert "archive_verdict" not in (out.extras or {})


def test_repeated_identical_approve_still_promotes(tmp_path: Path) -> None:
    archive = tmp_path / "repeat-approve.md"
    archive.write_text(
        "\n".join(
            [
                "VERDICT: APPROVE range=a..b head=b",
                "",
                "Recap:",
                "VERDICT: APPROVE range=a..b head=b",
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
    out = apply_archive_verdict_fallback_gated(result, purpose="review")
    assert out.ok is True
    assert out.extras.get("archive_verdict") == "APPROVE"


def test_gated_request_changes_stays_failed(tmp_path: Path) -> None:
    archive = tmp_path / "stargate-rc.md"
    archive.write_text(
        "## Body\n\nVERDICT: REQUEST_CHANGES range=a..b head=b\n",
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
    out = apply_archive_verdict_fallback_gated(result, purpose="review")
    assert out.ok is False


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


def test_loose_veto_bold_request_changes_with_canonical_approve(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "loose-bold.md"
    archive.write_text(
        "\n".join(
            [
                "```",
                "VERDICT: APPROVE",
                "```",
                "**VERDICT: REQUEST_CHANGES**",
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
    assert apply_archive_verdict_fallback(result).ok is False


def test_loose_veto_blockquote_and_indented_request_changes(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "loose-forms.md"
    archive.write_text(
        "\n".join(
            [
                "VERDICT: APPROVE range=a..b head=b",
                "> VERDICT: REQUEST_CHANGES",
                "  Verdict: request_changes — footnote",
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
    assert apply_archive_verdict_fallback(result).ok is False


def test_bare_verdict_colon_line_then_approve_does_not_promote(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "bare-colon.md"
    archive.write_text(
        "VERDICT:\nVERDICT: APPROVE range=a..b head=b\n",
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


def test_template_echo_plus_real_approve_does_not_promote(tmp_path: Path) -> None:
    archive = tmp_path / "template-plus-real.md"
    archive.write_text(
        "\n".join(
            [
                "VERDICT <APPROVE|REQUEST_CHANGES>",
                "VERDICT: APPROVE range=a..b head=b",
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
    assert apply_archive_verdict_fallback(result).ok is False


def _receipt_result(archive_uri: str) -> CdpGenerateResult:
    return CdpGenerateResult(
        ok=False,
        body="",
        execution_id="e" * 32,
        satellite_execution_id="s" * 32,
        prompt_uri="cortex://x",
        picker_model="opus",
        error=_RECEIPT_ERR,
        archive_uri=archive_uri,
    )


def test_loose_veto_final_verdict_prefix(tmp_path: Path) -> None:
    archive = tmp_path / "final-verdict.md"
    archive.write_text(
        "VERDICT: APPROVE range=a..b head=b\nFinal verdict: REQUEST_CHANGES\n",
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


def test_loose_veto_overall_verdict_heading(tmp_path: Path) -> None:
    archive = tmp_path / "overall.md"
    archive.write_text(
        "\n".join(
            [
                "VERDICT: APPROVE range=a..b head=b",
                "Overall VERDICT: REQUEST_CHANGES",
            ]
        ),
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


def test_loose_veto_numbered_list_item(tmp_path: Path) -> None:
    archive = tmp_path / "numbered.md"
    archive.write_text(
        "VERDICT: APPROVE range=a..b head=b\n1. Verdict: REQUEST_CHANGES\n",
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


def test_loose_veto_html_b_tag(tmp_path: Path) -> None:
    archive = tmp_path / "html-b.md"
    archive.write_text(
        "VERDICT: APPROVE range=a..b head=b\n<b>VERDICT: REQUEST_CHANGES</b>\n",
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


def test_loose_veto_html_p_strong_tag(tmp_path: Path) -> None:
    archive = tmp_path / "html-p.md"
    archive.write_text(
        "VERDICT: APPROVE range=a..b head=b\n"
        "<p><strong>Verdict:</strong> REQUEST_CHANGES</p>\n",
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


def test_body_veto_markdown_table_request_changes(tmp_path: Path) -> None:
    archive = tmp_path / "table.md"
    archive.write_text(
        "\n".join(
            [
                "VERDICT: APPROVE range=a..b head=b",
                "| Section | Outcome |",
                "| Verdict | REQUEST_CHANGES |",
            ]
        ),
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


def test_body_veto_bare_request_changes_token(tmp_path: Path) -> None:
    archive = tmp_path / "bare-rc.md"
    archive.write_text(
        "VERDICT: APPROVE range=a..b head=b\nScope needs REQUEST_CHANGES before land.\n",
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


def test_enum_echo_line_does_not_body_veto(tmp_path: Path) -> None:
    archive = tmp_path / "enum-echo.md"
    archive.write_text(
        "\n".join(
            [
                "VERDICT: APPROVE range=a..b head=b",
                "Allowed values: APPROVE | APPROVE_WITH_NITS | REQUEST_CHANGES",
            ]
        ),
        encoding="utf-8",
    )
    out = apply_archive_verdict_fallback(_receipt_result(f"file://{archive}"))
    assert out.ok is True
    assert out.extras.get("archive_verdict") == "APPROVE"


def test_smuggle_pipe_segment_request_changes_before_merge_does_not_promote(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "smuggle-rc-prose.md"
    archive.write_text(
        "\n".join(
            [
                "VERDICT: APPROVE range=a..b head=b",
                "APPROVE | REQUEST_CHANGES before merge",
            ]
        ),
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


def test_enum_echo_near_miss_substrings_do_not_promote(tmp_path: Path) -> None:
    archive = tmp_path / "enum-near-miss.md"
    archive.write_text(
        "\n".join(
            [
                "VERDICT: APPROVE range=a..b head=b",
                "REQUEST_CHANGES_PENDING | xAPPROVE",
                "APPROVE-ish | REQUEST_CHANGES",
            ]
        ),
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


def test_plain_loose_verdict_request_changes_does_not_promote(tmp_path: Path) -> None:
    archive = tmp_path / "loose-rc-plain.md"
    archive.write_text(
        "\n".join(
            [
                "VERDICT: APPROVE range=a..b head=b",
                "Verdict: REQUEST_CHANGES",
            ]
        ),
        encoding="utf-8",
    )
    assert apply_archive_verdict_fallback(_receipt_result(f"file://{archive}")).ok is False


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
