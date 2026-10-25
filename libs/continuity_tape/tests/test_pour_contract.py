"""Every pour.* refusal, plus a valid pour."""

from __future__ import annotations

import pytest
from continuity_tape.messages import seal_messages_sha256
from continuity_tape.pour_contract import PourRefused, validate_pour

pytestmark = pytest.mark.offline


def _ok() -> dict:
    messages = [{"role": "user", "content": "hello", "turn_index": 1}]
    return {
        "schema": "ulg.continuity.messages/1",
        "messages": messages,
        "meta": {
            "surface": "cursor",
            "transcript_id": "tid",
            "provenance": "raw",
            "source_sha256": "abc",
            "messages_sha256": seal_messages_sha256(messages),
            "coverage": "full",
            "sources": [
                {
                    "kind": "jsonl",
                    "producer": {
                        "id": "cursor-jsonl-extractor",
                        "kind": "extractor",
                        "version": None,
                    },
                    "window": {"lo": 1, "hi": 1, "total": 1},
                }
            ],
        },
    }


def test_valid_pour() -> None:
    env = validate_pour(_ok())
    assert env.meta.provenance == "raw"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d.update(schema="nope"), "pour.schema"),
        (lambda d: d["meta"].pop("provenance"), "pour.provenance_missing"),
        (lambda d: d["meta"].pop("sources"), "pour.producer_missing"),
        (lambda d: d["meta"].update(messages_sha256="0"), "pour.sha_mismatch"),
        (lambda d: d["meta"].update(coverage="tail"), "pour.coverage"),
        (lambda d: d["meta"].pop("source_sha256"), "pour.source_sha_missing"),
        (lambda d: d.update(messages=[]), "pour.hollow"),
        (
            lambda d: d["meta"]["sources"][0].update(
                window={"lo": 2, "hi": 1, "total": 9}
            ),
            "pour.window",
        ),
    ],
)
def test_refusal_codes(mutate, code: str) -> None:
    payload = _ok()
    mutate(payload)
    with pytest.raises(PourRefused) as exc:
        validate_pour(payload)
    assert exc.value.code == code


def _ok_grok_export(*, pages_sha: str, reassembled_sha: str, capture_sha: str) -> dict:
    payload = _ok()
    payload["meta"]["source_sha256"] = reassembled_sha
    payload["meta"]["sources"][0] = {
        "kind": "grok_bot_jsonl",
        "producer": {
            "id": "grokbot-jsonl-extractor",
            "kind": "extractor",
            "version": None,
        },
        "window": {"lo": 1, "hi": 1, "total": 1},
        "pages_sha256": pages_sha,
        "reassembled_jsonl_sha256": reassembled_sha,
    }
    payload["meta"]["capture"] = {
        "kind": "grok_export",
        "ref": "cortex://notes/x",
        "sha256": capture_sha,
        "pseudo_byte_exact": False,
    }
    return payload


def test_grok_export_capture_match_passes() -> None:
    pages = "b" * 64
    reassembled = "a" * 64
    assert (
        validate_pour(_ok_grok_export(
            pages_sha=pages, reassembled_sha=reassembled, capture_sha=pages
        )).meta.capture is not None
    )


def test_grok_export_capture_sha_not_pages_refuses() -> None:
    pages = "b" * 64
    reassembled = "a" * 64
    with pytest.raises(PourRefused) as exc:
        validate_pour(
            _ok_grok_export(
                pages_sha=pages,
                reassembled_sha=reassembled,
                capture_sha="c" * 64,
            )
        )
    assert exc.value.code == "pour.capture_inconsistent"
    assert "pages_sha256" in exc.value.message


def test_grok_export_reassembled_not_source_refuses() -> None:
    pages = "b" * 64
    payload = _ok_grok_export(
        pages_sha=pages, reassembled_sha="a" * 64, capture_sha=pages
    )
    payload["meta"]["source_sha256"] = "d" * 64
    with pytest.raises(PourRefused) as exc:
        validate_pour(payload)
    assert exc.value.code == "pour.capture_inconsistent"
    assert "reassembled_jsonl_sha256" in exc.value.message


@pytest.mark.parametrize(
    "field",
    ["pages_sha256", "reassembled_jsonl_sha256"],
)
def test_grok_export_missing_source_hashes_refuse(field: str) -> None:
    pages = "b" * 64
    reassembled = "a" * 64
    payload = _ok_grok_export(
        pages_sha=pages, reassembled_sha=reassembled, capture_sha=pages
    )
    payload["meta"]["sources"][0].pop(field)
    with pytest.raises(PourRefused) as exc:
        validate_pour(payload)
    assert exc.value.code == "pour.capture_inconsistent"
    assert field in exc.value.message


def test_capture_rules() -> None:
    payload = _ok()
    payload["meta"]["capture"] = {
        "kind": "dom_harvest",
        "ref": "cortex://notes/x",
        "sha256": "ab" * 32,
        "pseudo_byte_exact": False,
    }
    with pytest.raises(PourRefused) as exc:
        validate_pour(payload)
    assert exc.value.code == "pour.capture_invalid"
    payload["meta"]["capture"]["kind"] = "jsonl"
    payload["meta"]["capture"]["pseudo_byte_exact"] = False
    payload["meta"]["capture"]["sha256"] = "cd" * 32
    with pytest.raises(PourRefused) as mismatch:
        validate_pour(payload)
    assert mismatch.value.code == "pour.capture_inconsistent"
    payload["meta"]["source_sha256"] = "ab" * 32
    payload["meta"]["capture"] = {
        "kind": "jsonl",
        "ref": "cortex://notes/x",
        "sha256": "ab" * 32,
        "pseudo_byte_exact": False,
    }
    assert validate_pour(payload).meta.capture is not None


def test_model_validation_error_is_pour_schema() -> None:
    payload = _ok()
    payload["meta"]["surface"] = "not-a-surface"
    with pytest.raises(PourRefused) as exc:
        validate_pour(payload)
    assert exc.value.code == "pour.schema"


def test_import_without_stargate() -> None:
    assert callable(validate_pour)
