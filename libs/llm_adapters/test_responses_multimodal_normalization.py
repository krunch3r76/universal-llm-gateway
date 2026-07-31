"""Regression tests for Responses API multimodal input normalization."""

from __future__ import annotations

import pytest

from llm_adapters.responses.normalization import _normalize_input_content


def test_image_url_dict_to_input_image_with_detail() -> None:
    content = [
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,abc",
                "detail": "high",
            },
        }
    ]
    out = _normalize_input_content(content)
    assert out == [
        {
            "type": "input_image",
            "image_url": "data:image/png;base64,abc",
            "detail": "high",
        }
    ]


def test_image_url_string_form() -> None:
    content = [{"type": "image_url", "image_url": "https://example.com/x.png"}]
    out = _normalize_input_content(content)
    assert out == [{"type": "input_image", "image_url": "https://example.com/x.png"}]


def test_image_url_top_level_detail_when_inner_has_no_detail() -> None:
    content = [
        {
            "type": "image_url",
            "detail": "low",
            "image_url": {"url": "https://example.com/a.png"},
        }
    ]
    out = _normalize_input_content(content)
    assert out == [
        {
            "type": "input_image",
            "image_url": "https://example.com/a.png",
            "detail": "low",
        }
    ]


def test_mixed_text_and_image() -> None:
    content = [
        {"type": "text", "text": "Describe this"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,QQ=="},
        },
    ]
    out = _normalize_input_content(content)
    assert out == [
        {"type": "input_text", "text": "Describe this"},
        {"type": "input_image", "image_url": "data:image/jpeg;base64,QQ=="},
    ]


def test_input_image_pass_through() -> None:
    block = {"type": "input_image", "image_url": "data:image/png;base64,xx"}
    assert _normalize_input_content([block]) == [block]


def test_single_text_block_still_flattens() -> None:
    assert _normalize_input_content([{"type": "text", "text": "hi"}]) == "hi"


def test_string_content_pass_through() -> None:
    assert _normalize_input_content("plain") == "plain"


@pytest.mark.parametrize(
    ("bad", "match"),
    [
        (
            [{"type": "image_url", "image_url": {"detail": "high"}}],
            "non-empty string",
        ),
        ([{"type": "image_url", "image_url": ""}], "empty string"),
        ([{"type": "image_url"}], "string or object"),
    ],
)
def test_malformed_image_url_raises(bad: list, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _normalize_input_content(bad)
