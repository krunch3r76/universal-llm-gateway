"""Tests for nested CDP prompt authoring gates (a:37183)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    stage_cdp_prompt_with_skills,
)
from claude_bundles.nested_cdp_prompt_gate import (
    NestedCdpPromptGateError,
    enforce_nested_cdp_prompt_gates,
    enforce_retrieval_report,
    enforce_skeptic_chrome_refuse,
    hop_prompts_missing_report_bundles,
    is_skeptic_shaped,
)
from claude_bundles.sealed_cdp_prefix import ensure_review_reading_charter

_COMPLETE_REPORT = """# Retrieval report

## Queries
- scope=llm_prompting: adversarial spec review framing
- scope=suggestion_orientation: free-strategy expectancy

## Yields
- llm_prompting: weak_match=false (cross_encoder)
- suggestion_orientation: null (off-topic)
- prompt_injection: null (off-topic)
- agent_skills_research: weak_match=false

## Choice-to-evidence
| choice | evidence |
|---|---|
| free-strategy open | suggestion_orientation yield + my judgment |
| constraints last | llm_prompting post-prompting |
"""


_SKEPTIC_BODY = (
    "Genre: adversarial spec review. You are the G4 skeptic for todo:demo.\n"
    "Choose the attack route yourself.\n"
)


def _write_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "cortex-root"
    rel = "notes/system/ephemeral/retrieval-reports/demo.md"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_COMPLETE_REPORT, encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    # closeout_helpers caches? check — usually reads env each call
    return f"cortex://{rel}"


def test_skeptic_shape_detects_specimen_phrases() -> None:
    assert is_skeptic_shaped(_SKEPTIC_BODY)
    assert is_skeptic_shaped("gate_path=SKEPTIC on this hop")
    assert not is_skeptic_shaped("job=delivery-review on G6 branch diff")


def test_charter_not_injected_on_skeptic_purpose_review() -> None:
    out = ensure_review_reading_charter(_SKEPTIC_BODY, "review")
    assert "the packet carries the code under review" not in out
    delivery = ensure_review_reading_charter("Pin the lane branch diff.\n", "review")
    assert "the packet carries the code under review" in delivery


def test_chrome_on_skeptic_refuses() -> None:
    chrome = (
        "Review seat: the packet carries the code under review. "
        "Do not REJECT because you cannot check out a commit, run pytest, "
        "or run quality_gate.\n"
        f"{_SKEPTIC_BODY}"
    )
    with pytest.raises(NestedCdpPromptGateError) as exc:
        enforce_skeptic_chrome_refuse(chrome)
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
    enforce_nested_cdp_prompt_gates(body=body, purpose="review")


def test_incomplete_report_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cortex-root"
    rel = "notes/system/ephemeral/retrieval-reports/thin.md"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Retrieval report\n\n## Queries\n- x\n", encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    body = f"retrieval_report: cortex://{rel}\n{_SKEPTIC_BODY}"
    with pytest.raises(NestedCdpPromptGateError) as exc:
        enforce_retrieval_report(body)
    assert exc.value.code == "nested_cdp_retrieval_report_incomplete"


def test_hop_closeout_lists_missing_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uri = _write_report(tmp_path, monkeypatch)
    good = f"retrieval_report: {uri}\n{_SKEPTIC_BODY}"
    bad = _SKEPTIC_BODY
    assert hop_prompts_missing_report_bundles([good, bad, "plain ask"]) == [1]


def _passthrough_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worktree checkouts lack full .claude skill SOT; skip catalog load."""

    def _prepend(body: str, slugs: list[str]):
        del slugs
        return body, [], []

    monkeypatch.setattr(
        "claude_bundles.cowork_skill_delivery.prepend_cdp_dispatch_skills",
        _prepend,
    )


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
    # Charter must not land on skeptic bodies even under purpose=review.
    from implement_admission.closeout_helpers import cortex_files_root

    rel = staged.prompt_uri.removeprefix("cortex://")
    text = (cortex_files_root() / rel).read_text(encoding="utf-8")
    assert "the packet carries the code under review" not in text
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
