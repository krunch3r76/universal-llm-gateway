"""Unit tests for densify review frame builder and artifact resolver."""

from __future__ import annotations

import re

import pytest

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.densify_review_frame import (
    DENSIFY_ARTIFACT_MAX_BYTES,
    build_densify_review_prompt,
    densify_review_nonce,
    resolve_densify_artifact,
)


@pytest.mark.parametrize(
    ("uri", "code"),
    [
        ("workspaces:spec.md", "staged_draft_uri_unsupported"),
        ("/etc/hostname", "staged_draft_uri_unsupported"),
        ("tmp/x.md", "staged_draft_uri_unsupported"),
        ("workspaces://other-repo/tmp/x.md", "staged_draft_uri_unsupported"),
        (
            "cortex://notes/tmp/leg-d-frag.md#reasoning_trace",
            "staged_draft_uri_unsupported",
        ),
        ("cortex://../escape.md", "staged_draft_uri_unsupported"),
        ("cortex:///etc/hostname", "staged_draft_uri_unsupported"),
        ("cortex://notes/missing.md", "staged_draft_unreadable"),
        ("cortex://notes/tmp/leg-d-dir/", "staged_draft_uri_unsupported"),
        ("cortex://notes/tmp/leg-d-big.md", "staged_draft_too_large"),
        ("cortex://notes/tmp/leg-d-bad.md", "staged_draft_unreadable"),
        ("cortex://notes/tmp/leg-d-blank.md", "staged_draft_empty"),
    ],
)
def test_resolve_densify_artifact_refuses_each_shape(
    uri: str,
    code: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cortex = tmp_path / "cortex"
    ws_ulg = tmp_path / "ws" / "universal-llm-gateway"
    cortex.mkdir()
    ws_ulg.mkdir(parents=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex))
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))

    (tmp_path / "escape.md").write_text("outside", encoding="utf-8")
    (cortex / "notes" / "tmp").mkdir(parents=True)
    (cortex / "notes" / "tmp" / "leg-d-frag.md").write_text("frag", encoding="utf-8")
    blank_path = cortex / "notes" / "tmp" / "leg-d-blank.md"
    blank_path.write_text("  \n\t\n", encoding="utf-8")
    (cortex / "notes" / "tmp" / "leg-d-big.md").write_bytes(
        b"a" * (DENSIFY_ARTIFACT_MAX_BYTES + 1)
    )
    (cortex / "notes" / "tmp" / "leg-d-bad.md").write_bytes(b"\xff\xfe\x00")
    (cortex / "notes" / "tmp" / "leg-d-dir").mkdir()

    with pytest.raises(FrontierEndpointError) as exc:
        resolve_densify_artifact(uri, field="staged_draft_uri", request_id="ac5")
    assert exc.value.status_code == 422
    assert exc.value.field == "staged_draft_uri"
    assert exc.value.code == code
    assert exc.value.reason
    assert str(tmp_path) not in exc.value.reason


def test_resolve_densify_artifact_reads_each_scheme(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cortex = tmp_path / "cortex"
    ws_ulg = tmp_path / "ws" / "universal-llm-gateway"
    cortex.mkdir()
    ws_ulg.mkdir(parents=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex))
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))

    (cortex / "notes" / "tmp").mkdir(parents=True)
    (cortex / "notes" / "tmp" / "leg-d-pos.md").write_text(
        "cortex body\nline two\n", encoding="utf-8"
    )
    (ws_ulg / "tmp").mkdir(parents=True)
    (ws_ulg / "tmp" / "leg-d-pos.md").write_text("workspaces body\n", encoding="utf-8")

    assert (
        resolve_densify_artifact(
            "cortex://notes/tmp/leg-d-pos.md",
            field="staged_draft_uri",
            request_id="ac5",
        )
        == "cortex body\nline two\n"
    )
    assert (
        resolve_densify_artifact(
            "workspaces://universal-llm-gateway/tmp/leg-d-pos.md",
            field="staged_draft_uri",
            request_id="ac5",
        )
        == "workspaces body\n"
    )


def test_densify_review_nonce_filters_and_falls_back() -> None:
    assert densify_review_nonce("ac-7f3a9c") == "ac7f3a9c"
    assert re.fullmatch(r"[0-9a-f]{12}", densify_review_nonce("---"))
    lines = build_densify_review_prompt(
        draft_body="d",
        trace_body="t",
        nonce=densify_review_nonce("---"),
    ).split("\n")
    draft_pat = r"<composer_draft_[0-9a-f]{12}>"
    draft_tags = [ln for ln in lines if re.fullmatch(draft_pat, ln)]
    trace_tags = [
        ln for ln in lines if re.fullmatch(r"<reasoning_trace_[0-9a-f]{12}>", ln)
    ]
    assert len(draft_tags) == 1
    assert len(trace_tags) == 1
