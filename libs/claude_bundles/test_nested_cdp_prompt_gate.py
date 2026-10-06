"""Tests for nested CDP prompt authoring gates (a:37183 + review A1–A4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    stage_cdp_prompt_with_skills,
)
from claude_bundles.nested_cdp_prompt_gate import (
    NestedCdpPromptGateError,
    empty_report_sections,
    enforce_nested_cdp_prompt_gates,
    enforce_retrieval_report,
    enforce_skeptic_chrome_refuse,
    hop_prompts_missing_report_bundles,
    is_nested_width_shaped,
    is_skeptic_shaped,
    resolve_cortex_uri,
)
from claude_bundles.sealed_cdp_prefix import ensure_review_reading_charter

_COMPLETE_REPORT = """# Retrieval report
target: todo:demo

## Queries
- scope=llm_prompting: adversarial spec review framing

## Yields
- llm_prompting: weak_match=false (cross_encoder)
- suggestion_orientation: null (off-topic)

## Choice-to-evidence
| choice | evidence |
|---|---|
| free-strategy open | suggestion_orientation yield |
"""


_SKEPTIC_BODY = (
    "Genre: adversarial spec review. You are the G4 skeptic for todo:demo.\n"
    "Choose the attack route yourself.\n"
)


def _write_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: str = _COMPLETE_REPORT,
    rel: str = "notes/system/ephemeral/retrieval-reports/demo.md",
) -> str:
    root = tmp_path / "cortex-root"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    return f"cortex://{rel}"


def _passthrough_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worktree checkouts lack full .claude skill SOT; skip catalog load."""

    def _prepend(body: str, slugs: list[str], **kwargs: object):
        del slugs, kwargs
        return body, [], []

    monkeypatch.setattr(
        "claude_bundles.cowork_skill_delivery.prepend_cdp_dispatch_skills",
        _prepend,
    )


def test_skeptic_shape_line_anchored_not_mid_sentence() -> None:
    assert is_skeptic_shaped(_SKEPTIC_BODY)
    assert is_skeptic_shaped("gate_path=SKEPTIC\non this hop\n")
    assert not is_skeptic_shaped(
        "Summarize what the G4 skeptic review found yesterday\n"
    )
    assert not is_skeptic_shaped("job=delivery-review on G6 branch diff\n")


def test_meta_review_prompt_does_not_arm() -> None:
    """A1 — ordinary ask that only mentions the vocabulary must not arm."""
    meta = (
        "Review the nested_cdp_prompt_gate design. "
        "The packet mentions gate_path=SKEPTIC|SKETCH|ACTIVE as prose.\n"
        "Pager-notify recommends saying G4 skeptic review.\n"
    )
    assert not is_skeptic_shaped(meta)
    assert not is_nested_width_shaped(meta)


def test_inlined_skill_excerpt_does_not_arm() -> None:
    """A1 — skill prose mentioning G2/G4 mid-sentence is not an author declaration."""
    inline = (
        "<skills_inline>\n"
        '<skill slug="consult-routing">G2 frame and G4 skeptic read ACTIVE.</skill>\n'
        "</skills_inline>\n"
        "Pin the ordinary ask.\n"
    )
    assert not is_skeptic_shaped(inline)
    assert not is_nested_width_shaped(inline)


def test_retrieval_report_arm_live_after_prefix() -> None:
    """A1 — (?m) keeps retrieval_report: matching after slash skill lines."""
    body = "/reasoning-posture\nretrieval_report: cortex://notes/x.md\nPin ask.\n"
    assert is_nested_width_shaped(body)


def test_charter_not_injected_on_skeptic_purpose_review() -> None:
    out = ensure_review_reading_charter(_SKEPTIC_BODY, "review")
    assert "the packet carries the code under review" not in out.casefold()
    delivery = ensure_review_reading_charter("Pin the lane branch diff.\n", "review")
    assert "the packet carries the code under review" in delivery.casefold()


def test_charter_uses_author_body_not_merged_skills() -> None:
    """A1 — merged skill text mentioning G4 must not suppress delivery charter."""
    author = "Pin the lane branch diff.\n"
    merged = (
        "/reasoning-posture\n"
        "G4 skeptic review is documented in consult-routing.\n" + author
    )
    out = ensure_review_reading_charter(merged, "review", author_body=author)
    assert "the packet carries the code under review" in out.casefold()


def test_chrome_on_skeptic_refuses_casefold() -> None:
    chrome = (
        "Review seat: The packet carries the code under review. "
        "Do not REJECT because you cannot check out a commit, run pytest, "
        "or run quality_gate.\n"
        f"{_SKEPTIC_BODY}"
    )
    with pytest.raises(NestedCdpPromptGateError) as exc:
        enforce_skeptic_chrome_refuse(author_body=_SKEPTIC_BODY, body=chrome)
    assert exc.value.code == "nested_cdp_skeptic_chrome"


def test_missing_sidecar_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "empty-root"))
    with pytest.raises(NestedCdpPromptGateError) as exc:
        enforce_retrieval_report(_SKEPTIC_BODY)
    assert exc.value.code == "nested_cdp_retrieval_report_required"


def test_happy_path_with_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uri = _write_report(tmp_path, monkeypatch)
    body = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    assert enforce_retrieval_report(body) == uri
    enforce_nested_cdp_prompt_gates(body=body, author_body=body, purpose="review")


def test_friction_target_binds_delivery_review_width(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _COMPLETE_REPORT.replace("target: todo:demo", "target: friction:37313")
    uri = _write_report(tmp_path, monkeypatch, body=report)
    body = (
        f"retrieval_report: {uri}\n"
        "job=delivery-review\n"
        "gate_path=review\n"
        "friction:37313\n"
    )
    assert enforce_retrieval_report(body) == uri


def test_report_target_mismatch_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = _COMPLETE_REPORT.replace("target: todo:demo", "target: todo:other")
    uri = _write_report(tmp_path, monkeypatch, body=bad)
    body = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    with pytest.raises(NestedCdpPromptGateError) as exc:
        enforce_retrieval_report(body)
    assert exc.value.code == "nested_cdp_retrieval_report_target_mismatch"
    msg = str(exc.value)
    assert "todo:other" in msg
    assert "todo:demo" in msg


def test_report_target_multi_token_refuses_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = _COMPLETE_REPORT.replace(
        "target: todo:demo", "target: todo:foo plus prose"
    )
    uri = _write_report(tmp_path, monkeypatch, body=bad)
    body = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    with pytest.raises(NestedCdpPromptGateError) as exc:
        enforce_retrieval_report(body)
    assert exc.value.code == "nested_cdp_retrieval_report_target_invalid"


def test_report_target_single_token_still_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uri = _write_report(tmp_path, monkeypatch)
    body = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    assert enforce_retrieval_report(body) == uri


def test_report_target_required_when_line_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_target = _COMPLETE_REPORT.replace("target: todo:demo\n", "")
    uri = _write_report(tmp_path, monkeypatch, body=no_target)
    body = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    with pytest.raises(NestedCdpPromptGateError) as exc:
        enforce_retrieval_report(body)
    assert exc.value.code == "nested_cdp_retrieval_report_target_required"


def test_yields_subheading_table_is_not_empty_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:37914 — ``###`` after ``## Yields`` is in-section content, not a terminator."""
    assert empty_report_sections("## Yields\n\n### scope\n| table |") == []
    report = """# Retrieval report
target: todo:demo

## Queries
- q

## Yields

### llm_prompting
| table |

## Choice-to-evidence
| choice | evidence |
"""
    uri = _write_report(tmp_path, monkeypatch, body=report)
    body = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    assert enforce_retrieval_report(body) == uri


def test_report_empty_section_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thin = """# Retrieval report
target: todo:demo

## Queries

## Yields
- x

## Choice-to-evidence
- y
"""
    uri = _write_report(tmp_path, monkeypatch, body=thin)
    body = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    with pytest.raises(NestedCdpPromptGateError) as exc:
        enforce_retrieval_report(body)
    assert exc.value.code == "nested_cdp_retrieval_report_empty_section"


def test_incomplete_report_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uri = _write_report(
        tmp_path,
        monkeypatch,
        body="# Retrieval report\ntarget: todo:demo\n\n## Queries\n- x\n",
    )
    body = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    with pytest.raises(NestedCdpPromptGateError) as exc:
        enforce_retrieval_report(body)
    assert exc.value.code == "nested_cdp_retrieval_report_incomplete"


def test_path_escape_uses_is_relative_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A4 — /data/files2 must not pass under root /data/files."""
    root = tmp_path / "files"
    root.mkdir()
    sibling = tmp_path / "files2" / "x.md"
    sibling.parent.mkdir()
    sibling.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    # Craft a relative path that resolves outside via .. — blocked by is_relative_to
    with pytest.raises(NestedCdpPromptGateError) as exc:
        resolve_cortex_uri("cortex://../files2/x.md")
    assert exc.value.code == "nested_cdp_retrieval_report_escape"


def test_hop_closeout_lists_missing_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uri = _write_report(tmp_path, monkeypatch)
    good = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    bad = _SKEPTIC_BODY
    assert hop_prompts_missing_report_bundles([good, bad, "plain ask"]) == [1]


def test_stage_refuses_skeptic_without_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex-root"))
    _passthrough_skills(monkeypatch)
    with pytest.raises(CdpStagingError) as exc:
        stage_cdp_prompt_with_skills(
            execution_id="exec-skeptic-no-report",
            prompt_text=_SKEPTIC_BODY,
            purpose="review",
        )
    assert exc.value.code == "nested_cdp_retrieval_report_required"


def test_stage_admits_skeptic_with_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uri = _write_report(tmp_path, monkeypatch)
    _passthrough_skills(monkeypatch)
    body = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-skeptic-ok",
        prompt_text=body,
        purpose="review",
    )
    assert staged.prompt_uri.startswith("cortex://")
    from implement_admission.closeout_helpers import cortex_files_root

    rel = staged.prompt_uri.removeprefix("cortex://")
    text = (cortex_files_root() / rel).read_text(encoding="utf-8")
    assert "the packet carries the code under review" not in text.casefold()
    assert "retrieval_report:" in text


def test_stage_refuses_author_chrome_on_skeptic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uri = _write_report(tmp_path, monkeypatch)
    _passthrough_skills(monkeypatch)
    chrome = (
        "Review seat: the packet carries the code under review. "
        "Do not REJECT because you cannot check out a commit, run pytest, "
        f"or run quality_gate.\nretrieval_report: {uri}\n{_SKEPTIC_BODY}"
    )
    with pytest.raises(CdpStagingError) as exc:
        stage_cdp_prompt_with_skills(
            execution_id="exec-skeptic-chrome",
            prompt_text=chrome,
            purpose="ask",
        )
    assert exc.value.code == "nested_cdp_skeptic_chrome"


def test_stage_meta_review_admits_without_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A1 false-positive falsifier — mention vocabulary, expect admit."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex-root"))
    _passthrough_skills(monkeypatch)
    meta = (
        "Summarize what the G4 skeptic review found yesterday.\n"
        "Do not treat this as a conductor nested width seat.\n"
    )
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-meta-review",
        prompt_text=meta,
        purpose="review",
    )
    assert staged.prompt_uri.startswith("cortex://")


def test_followup_resolve_refuses_skeptic_without_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2 — cse_session follow-up path runs the same gate."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex-root"))
    from cdp_ask.runner import resolve_followup_prompt

    class _Req:
        prompt_text = _SKEPTIC_BODY
        prompt_uri = None
        prompt_path = None

    with pytest.raises(NestedCdpPromptGateError) as exc:
        resolve_followup_prompt(_Req())
    assert exc.value.code == "nested_cdp_retrieval_report_required"


def test_followup_resolve_admits_with_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uri = _write_report(tmp_path, monkeypatch)
    from cdp_ask.runner import resolve_followup_prompt

    class _Req:
        prompt_text = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
        prompt_uri = None
        prompt_path = None

    text = resolve_followup_prompt(_Req())
    assert "retrieval_report:" in text
